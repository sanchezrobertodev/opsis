"""Replicate adapter (HTTP API).

Endpoints are `owner/model` (official models, latest version) or `owner/model:version`. Outputs are
deleted by Replicate after an hour, which is why ingestion runs immediately after success.
"""

from __future__ import annotations

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
    looks_like_moderation,
    require_key,
    route_outputs,
    staged_ref,
)
from studio.registry.schema import Route

API = "https://api.replicate.com/v1"
FILE_TTL = timedelta(hours=20)  # Files API objects live ~24 h


class ReplicateAdapter(ProviderAdapter):
    key = "replicate"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        token = require_key("Replicate", get_settings().replicate_api_token)
        return httpx.AsyncClient(base_url=API, timeout=timeout, transport=self._transport,
                                 headers={"Authorization": f"Bearer {token}"})

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        async def upload(m: ResolvedMedia) -> tuple[str, datetime]:
            data = await anyio.Path(m.path).read_bytes()
            try:
                async with self._client(timeout=300) as c:
                    r = await c.post("/files", files={"content": (m.path.name, data, m.mime)})
            except httpx.HTTPError as exc:
                raise ProviderError(ErrorClass.transient, f"Replicate upload failed: {exc!r}") from exc
            if r.status_code not in (200, 201):
                raise http_error("Replicate upload", r)
            return r.json()["urls"]["get"], datetime.now(UTC) + FILE_TTL

        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(await staged_ref(self.key, m, upload))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if ":" in endpoint:
            path, body = "/predictions", {"version": endpoint.split(":", 1)[1], "input": payload}
        else:
            path, body = f"/models/{endpoint}/predictions", {"input": payload}
        try:
            async with self._client() as c:
                r = await c.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise AmbiguousSubmitError(f"Replicate submit timed out: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"Replicate submit failed before sending: {exc!r}") from exc
        if r.status_code not in (200, 201):
            raise http_error("Replicate", r)
        body = r.json()
        return SubmitResult(job_id=body["id"], raw={"status": body.get("status")})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        try:
            async with self._client() as c:
                r = await c.get(f"/predictions/{job_id}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"Replicate status failed: {exc!r}") from exc
        if r.status_code != 200:
            raise http_error("Replicate", r)
        p = r.json()
        status = p.get("status")
        usage = {"predict_time": (p.get("metrics") or {}).get("predict_time")}
        if status == "starting":
            return PollResult(status="queued", raw={"status": status})
        if status == "processing":
            return PollResult(status="running", raw={"status": status})
        if status in ("canceled", "aborted"):  # aborted: the Cancel-After deadline hit before it started
            return PollResult(status="canceled", usage=usage)
        if status == "failed":
            err = str(p.get("error") or "prediction failed")
            cls = ErrorClass.moderation if looks_like_moderation(err) else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"Replicate: {err[:800]}", usage=usage)
        outputs = route_outputs(p, route.output)
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="Replicate finished without an output", usage=usage)
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        try:
            async with self._client() as c:
                r = await c.post(f"/predictions/{job_id}/cancel")
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        # Replicate's file URLs (api.replicate.com and replicate.delivery) may require the token.
        # Send it only to Replicate's own hosts.
        headers = {}
        host = httpx.URL(output.url or "").host
        if host == "api.replicate.com" or host.endswith("replicate.delivery"):
            headers["Authorization"] = f"Bearer {get_settings().replicate_api_token}"
        content_type = await download_url(output.url or "", dest, headers=headers)
        return best_mime(output.mime, content_type, kind=output.kind)
