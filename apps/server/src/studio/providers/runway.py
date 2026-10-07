"""Runway API adapter (Gen-4.5, Gen-4 Turbo, Aleph 2 video-to-video, Act-Two).

Route endpoints are API paths (`image_to_video`, `text_to_video`, `video_to_video`, ...); the model id is a
fixed value on the route mode (e.g. `fixed: {model: gen4.5}`), so new Runway models are registry edits.
Every task is polled at /tasks/{id}; Runway asks clients not to poll faster than every 5 seconds.
Contract verified 2026-10-06 against https://docs.dev.runwayml.com/openapi.json (version 2024-11-06).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import (
    AmbiguousSubmitError,
    PollResult,
    ProviderAdapter,
    ProviderError,
    ProviderOutput,
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import (
    best_mime,
    download_url,
    http_error,
    require_key,
    route_outputs,
    staged_ref,
)
from studio.registry.schema import Route

API = "https://api.dev.runwayml.com/v1"
API_VERSION = "2024-11-06"
INLINE_IMAGE_LIMIT = 3 * 1024 * 1024  # data URIs cap at 5 MB after base64 (+33%)
UPLOAD_TTL = timedelta(hours=23)  # runway:// URIs live 24 h


def classify_failure(code: str) -> ErrorClass:
    """Runway failureCode -> what to do next (per their error guide)."""
    if code.startswith("SAFETY") or code == "INPUT_PREPROCESSING.SAFETY.TEXT":
        return ErrorClass.moderation  # input safety failures are not refunded; never retry
    if code == "ASSET.INVALID":
        return ErrorClass.invalid_request
    if code.startswith(("INTERNAL", "THIRD_PARTY", "INPUT_PREPROCESSING.INTERNAL")) or not code:
        return ErrorClass.transient
    return ErrorClass.internal


class RunwayAdapter(ProviderAdapter):
    key = "runway"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        key = require_key("Runway", get_settings().runway_api_key)
        return httpx.AsyncClient(base_url=API, timeout=timeout, transport=self._transport, headers={
            "Authorization": f"Bearer {key}", "X-Runway-Version": API_VERSION})

    async def _upload(self, m: ResolvedMedia) -> tuple[str, datetime]:
        """Ephemeral upload: ask for a presigned form, post the file to it, use the runway:// URI."""
        try:
            async with self._client() as c:
                r = await c.post("/uploads", json={"filename": m.path.name, "type": "ephemeral"})
            if r.status_code not in (200, 201):
                raise http_error("Runway upload", r)
            spec = r.json()
            data = await anyio.Path(m.path).read_bytes()
            async with httpx.AsyncClient(timeout=300, transport=self._transport) as raw:
                up = await raw.post(spec["uploadUrl"], data=spec.get("fields", {}),
                                    files={"file": (m.path.name, data, m.mime)})
            if up.status_code not in (200, 201, 204):
                raise ProviderError(ErrorClass.transient, f"Runway upload storage returned {up.status_code}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"Runway upload failed: {exc!r}") from exc
        return spec["runwayUri"], datetime.now(UTC) + UPLOAD_TTL

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            size = (await anyio.Path(m.path).stat()).st_size
            if m.kind == "image" and size <= INLINE_IMAGE_LIMIT:
                data = await anyio.Path(m.path).read_bytes()
                ref = f"data:{m.mime};base64,{base64.b64encode(data).decode()}"
            else:
                ref = await staged_ref(self.key, m, self._upload)
            staged.setdefault(m.role, []).append(ref)
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint.lstrip('/')}", json=payload)
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"Runway submit timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"Runway submit failed before sending: {exc!r}") from exc
        if r.status_code not in (200, 201):
            raise http_error("Runway", r)
        body = r.json()
        return SubmitResult(job_id=body["id"], raw={"endpoint": endpoint, "estimated_cost": body.get("estimatedCost")})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        try:
            async with self._client() as c:
                r = await c.get(f"/tasks/{job_id}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"Runway status failed: {exc!r}") from exc
        if r.status_code != 200:
            raise http_error("Runway", r)
        t = r.json()
        status = t.get("status")
        credits = (t.get("cost") or {}).get("credits") if isinstance(t.get("cost"), dict) else None
        # Runway sells credits at $0.01 each (docs.dev.runwayml.com/guides/pricing, checked 2026-10-06)
        usage = {"credits": credits, "cost_usd": round(credits * 0.01, 6)} if credits is not None else {}
        if status in ("PENDING", "THROTTLED"):
            return PollResult(status="queued", raw={"status": status})
        if status == "RUNNING":
            progress = t.get("progress")
            return PollResult(status="running", progress=float(progress) if progress is not None else None)
        if status == "CANCELLED":
            return PollResult(status="canceled", usage=usage)
        if status == "FAILED":
            code = str(t.get("failureCode") or "")
            msg = str(t.get("failure") or "task failed")
            return PollResult(status="failed", error_class=classify_failure(code),
                              error_message=f"Runway {code or 'failure'}: {msg}"[:900], usage=usage)
        outputs = route_outputs(t, route.output)
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="Runway task succeeded without an output", usage=usage)
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        try:
            async with self._client() as c:
                r = await c.delete(f"/tasks/{job_id}")
        except httpx.HTTPError:
            return False
        return r.status_code in (200, 204)

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        content_type = await download_url(output.url or "", dest)
        return best_mime(output.mime, content_type, kind=output.kind)
