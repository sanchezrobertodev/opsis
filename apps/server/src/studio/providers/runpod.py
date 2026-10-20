"""RunPod serverless adapter: Public Endpoints (pay-per-use model APIs) and our own endpoints alike.

Contract (docs.runpod.io/serverless/endpoints/operation-reference, checked 2026-10-06):
- POST /v2/{endpoint}/run {"input": {...}} -> {"id", "status": "IN_QUEUE"}
- GET  /v2/{endpoint}/status/{id} -> {"status": IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED|CANCELLED|TIMED_OUT,
  "output": {...}, "error"?}. The result is kept 30 minutes after completion; media URLs live 7 days.
- POST /v2/{endpoint}/cancel/{id}
Routes give the endpoint id per mode and the output path (`output.video_url`, `output.result`, ...).
Public endpoints report what they charged in `output.cost` (USD), which becomes the take's actual cost.

Inputs must be web links. Files go up through fal storage (reusing fal's upload cache) when FAL_KEY is set;
without it, small images are sent inline as data URIs and anything else is refused with an explanation.
`/retry` is never used: a failed paid job is never resubmitted automatically.
"""

from __future__ import annotations

import base64
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
    ResolvedMedia,
    SubmitResult,
)
from studio.providers.common import (
    http_error,
    looks_like_moderation,
    require_key,
    route_outputs,
    submit_error,
    submit_failure,
)
from studio.registry.schema import Route

API = "https://api.runpod.ai/v2"
INLINE_IMAGE_LIMIT = 6 * 1024 * 1024  # /run bodies cap at 10 MB, and base64 adds a third


class RunPodAdapter(ProviderAdapter):
    key = "runpod"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        key = require_key("RunPod", get_settings().runpod_api_key)
        return httpx.AsyncClient(base_url=API, timeout=timeout, transport=self._transport,
                                 headers={"Authorization": f"Bearer {key}"})

    async def _public_url(self, route: Route, m: ResolvedMedia) -> str:
        if get_settings().fal_key:
            from studio.providers.factory import get_adapter

            refs = await get_adapter("fal").stage_inputs(route, [m])
            return refs[m.role][0]
        size = (await anyio.Path(m.path).stat()).st_size
        if m.kind == "image" and size <= INLINE_IMAGE_LIMIT:
            data = await anyio.Path(m.path).read_bytes()
            return f"data:{m.mime};base64,{base64.b64encode(data).decode()}"
        raise ProviderError(ErrorClass.invalid_request,
                            "RunPod needs a web link for this file. Add FAL_KEY to .env; it's only used to host "
                            "input files for providers that can't take uploads.")

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(await self._public_url(route, m))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint}/run", json={"input": payload})
        except httpx.HTTPError as exc:
            raise submit_failure("RunPod", exc) from exc
        if r.status_code not in (200, 201):
            raise submit_error("RunPod", r)
        body = r.json()
        job = body.get("id")
        if not job:
            raise AmbiguousSubmitError(f"RunPod accepted the request but returned no job id: {str(body)[:300]}")
        return SubmitResult(job_id=f"{endpoint}::{job}", raw={"endpoint": endpoint, "status": body.get("status")})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        endpoint, _, job = job_id.partition("::")
        try:
            async with self._client() as c:
                r = await c.get(f"/{endpoint}/status/{job}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, f"RunPod status failed: {exc!r}") from exc
        if r.status_code == 404:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="RunPod no longer has this job (results are kept 30 minutes)")
        if r.status_code != 200:
            raise http_error("RunPod", r)
        t = r.json()
        status = t.get("status")
        output = t.get("output") if isinstance(t.get("output"), dict) else {}
        usage = {"cost_usd": output["cost"]} if isinstance(output.get("cost"), int | float) else {}
        if status == "IN_QUEUE":
            return PollResult(status="queued", raw={"status": status})
        if status == "IN_PROGRESS":
            return PollResult(status="running")
        if status == "CANCELLED":
            return PollResult(status="canceled", usage=usage)
        if status == "TIMED_OUT":
            return PollResult(status="failed", error_class=ErrorClass.timeout,
                              error_message="RunPod timed out before the job finished", usage=usage)
        if status == "FAILED":
            msg = str(t.get("error") or output.get("error") or "the job failed")[:900]
            cls = ErrorClass.moderation if looks_like_moderation(msg) else ErrorClass.internal
            return PollResult(status="failed", error_class=cls, error_message=f"RunPod: {msg}", usage=usage)
        if status != "COMPLETED":
            return PollResult(status="running", raw={"status": status})
        outputs = route_outputs(t, route.output)
        if not outputs:
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="RunPod finished without a result link", usage=usage)
        return PollResult(status="succeeded", progress=1.0, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        endpoint, _, job = job_id.partition("::")
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint}/cancel/{job}")
        except httpx.HTTPError:
            return False
        return r.status_code == 200

