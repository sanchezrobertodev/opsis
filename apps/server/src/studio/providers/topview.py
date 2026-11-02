"""Topview API Platform predictions (not the legacy Topview subscription API).

Contract: https://platform.topview.ai/docs/models/topview/topview-t1, checked 2026-11-01.
Never resubmit an uncertain exchange, even though the service supports idempotency keys.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

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
from studio.providers.common import http_error, require_key, route_outputs, submit_error, submit_failure
from studio.registry.schema import Route

API = "https://platform-backend.topview.ai/api/v1"


class TopviewAdapter(ProviderAdapter):
    key = "topview"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        key = require_key("Topview API Platform", get_settings().topview_api_key)
        return httpx.AsyncClient(base_url=API, timeout=45, transport=self._transport,
                                 headers={"Authorization": f"Bearer {key}"})

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        if media:
            raise ProviderError(ErrorClass.invalid_request, "Topview currently supports text-to-video only")
        return {}

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        try:
            async with self._client() as c:
                r = await c.post(f"/{endpoint.lstrip('/')}", json=payload,
                                 headers={"Idempotency-Key": client_ref})
        except httpx.HTTPError as exc:
            raise submit_failure("Topview", exc) from exc
        # A gateway/server error or idempotency conflict can hide an accepted prediction.
        if r.status_code >= 500 or r.status_code in (408, 409):
            raise AmbiguousSubmitError(f"Topview submit returned {r.status_code}; reconcile before retrying")
        if r.status_code not in (200, 201, 202):
            raise submit_error("Topview", r)
        try:
            data = r.json()["data"]
            job_id = data["id"]
            if not isinstance(job_id, str) or not job_id.strip() or job_id in (".", ".."):
                raise ValueError("invalid prediction ID")
        except (ValueError, KeyError, TypeError) as exc:
            raise AmbiguousSubmitError("Topview accepted the request without a valid task ID; do not resubmit") from exc
        return SubmitResult(job_id=job_id, raw={"endpoint": endpoint})

    async def poll(self, route: Route, job_id: str) -> PollResult:
        try:
            async with self._client() as c:
                r = await c.get(f"/predictions/{quote(job_id, safe='')}")
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorClass.transient, "Topview status temporarily unreachable") from exc
        if r.status_code != 200:
            raise http_error("Topview status", r)
        try:
            data = r.json()["data"]
            if not isinstance(data, dict):
                raise ValueError("invalid prediction")
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(ErrorClass.route_unavailable, "Topview returned an unreadable task; do not resubmit") from exc
        if data.get("id") != job_id:
            raise ProviderError(ErrorClass.route_unavailable, "Topview returned a different task; reconcile before retrying")
        status = data.get("status")
        if status in ("created", "processing"):
            return PollResult(status="queued" if status == "created" else "running")
        if status == "payment_required":
            raise ProviderError(ErrorClass.route_unavailable,
                                "Topview requires payment in the original workspace. Settle and recover this task; do not resubmit.")
        if status not in ("completed", "failed"):
            raise ProviderError(ErrorClass.route_unavailable, "Topview task state is unknown; do not resubmit")
        # Only final, validated charges are handed to the ledger, including a confirmed $0 refund.
        try:
            charge = data["charged_usd"]
            if not isinstance(charge, str):
                raise ValueError("charge must be a decimal string")
            amount = Decimal(charge)
            if not amount.is_finite() or amount < 0 or amount > Decimal("1e9"):
                raise ValueError("invalid charge")
            expected = "CAPTURED" if status == "completed" else "RELEASED"
            if data.get("billing_state") != expected or (status == "failed" and amount != 0):
                raise ValueError("billing not settled")
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ProviderError(ErrorClass.route_unavailable,
                                "Topview billing is unsettled or unreadable; reconcile this task before retrying") from exc
        usage = {"cost_usd": float(amount), "cost_estimated": False}
        if status == "failed":
            return PollResult(status="failed", error_class=ErrorClass.internal,
                              error_message="Topview generation failed; its full hold was released", usage=usage)
        outputs = route_outputs(data, route.output)
        if not outputs or any(urlsplit(o.url or "").scheme != "https" for o in outputs):
            raise ProviderError(ErrorClass.route_unavailable,
                                "Topview completed and billed, but its output is missing or invalid; recover this task")
        return PollResult(status="succeeded", progress=1, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        # No cancellation endpoint is documented. Never delete media or pretend billing was stopped.
        return False
