"""fal.ai adapter (queue API via the official `fal-client` SDK, except submit).

Route endpoints are fal endpoint ids such as `fal-ai/veo3.1/image-to-video`. fal serves status/result
under the base app id (`fal-ai/veo3.1`), which the SDK derives itself; job ids store `app::request_id`.
Inputs are uploaded to fal storage once per asset and the URL is reused while fresh.

Submit is one plain POST to the queue: the SDK retries a submit on any dropped connection, which can start the same
paid job several times (fal-client 1.0.3), and a submit whose outcome is unknown must never be resent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fal_client
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
    classify_http,
    download_url,
    looks_like_moderation,
    require_key,
    route_outputs,
    staged_ref,
    submit_error,
    submit_failure,
)
from studio.registry.schema import Route

QUEUE_URL = "https://queue.fal.run/"
SUBMIT_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
UPLOAD_TTL = timedelta(days=7)  # conservative; fal keeps uploads longer, but we re-upload rather than risk a 404


def app_base(endpoint: str) -> str:
    parts = endpoint.split("/")
    if parts[0] in ("workflows", "comfy"):
        return "/".join(parts[:3])
    return "/".join(parts[:2])


class FalAdapter(ProviderAdapter):
    key = "fal"

    def __init__(self, client: Any | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = client
        self._transport = transport

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = fal_client.AsyncClient(key=require_key("fal.ai", get_settings().fal_key))
        return self._client

    def _error(self, exc: Exception, action: str) -> ProviderError:
        if isinstance(exc, fal_client.FalClientHTTPError):
            text = f"{exc.message} {exc.error_type or ''}".strip()
            cls = ErrorClass.moderation if looks_like_moderation(text) else classify_http(exc.status_code, text)
            return ProviderError(cls, f"fal.ai {action} failed ({exc.status_code}): {text[:800]}")
        if isinstance(exc, ProviderError):
            return exc
        return ProviderError(ErrorClass.transient, f"fal.ai {action} failed: {exc!r}")

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        async def upload(m: ResolvedMedia) -> tuple[str, datetime]:
            try:
                url = await self.client.upload_file(m.path)
            except Exception as exc:
                raise self._error(exc, "upload") from exc
            return url, datetime.now(UTC) + UPLOAD_TTL

        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(await staged_ref(self.key, m, upload))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        key = require_key("fal.ai", get_settings().fal_key)
        try:
            async with httpx.AsyncClient(timeout=SUBMIT_TIMEOUT, transport=self._transport) as c:
                r = await c.post(f"{QUEUE_URL}{endpoint}", json=payload, headers={"Authorization": f"Key {key}"})
        except httpx.HTTPError as exc:
            raise submit_failure("fal.ai", exc) from exc
        if r.status_code not in (200, 201, 202):
            raise submit_error("fal.ai", r)
        request_id = r.json().get("request_id")
        if not request_id:
            raise AmbiguousSubmitError(f"fal.ai accepted the request but returned no request id: {r.text[:300]}")
        return SubmitResult(job_id=f"{app_base(endpoint)}::{request_id}",
                            raw={"request_id": request_id, "endpoint": endpoint})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        app, _, request_id = job_id.partition("::")
        try:
            status = await self.client.status(app, request_id, with_logs=True)
        except Exception as exc:
            raise self._error(exc, "status") from exc
        if isinstance(status, fal_client.Queued):
            return PollResult(status="queued", queue_position=status.position + 1, raw={"queue_position": status.position})
        if isinstance(status, fal_client.InProgress):
            return PollResult(status="running", raw={"logs": (status.logs or [])[-3:]})
        assert isinstance(status, fal_client.Completed)
        if status.error:
            text = f"{status.error} {status.error_type or ''}"
            cls = ErrorClass.moderation if looks_like_moderation(text) else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"fal.ai: {text[:800]}",
                              usage=status.metrics or {})
        try:
            result = await self.client.result(app, request_id)
        except Exception as exc:
            raise self._error(exc, "result") from exc
        outputs = route_outputs(result, route.output)
        if not outputs:
            if looks_like_moderation(str(result)):
                return PollResult(status="failed", error_class=ErrorClass.moderation,
                                  error_message="fal.ai returned no output (content filtered)")
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message=f"fal.ai returned no output at {route.output.path!r}")
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=status.metrics or {},
                          raw={"result_keys": sorted(result) if isinstance(result, dict) else None})

    async def cancel(self, route: Route, job_id: str) -> bool:
        app, _, request_id = job_id.partition("::")
        try:
            await self.client.cancel(app, request_id)
        except Exception:
            return False
        return True

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        content_type = await download_url(output.url or "", dest)
        return best_mime(output.mime, content_type, kind=output.kind)
