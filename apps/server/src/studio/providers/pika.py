"""Pika's asynchronous media API. Quotes are free; generation POSTs are never replayed automatically."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

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
    http_error,
    require_key,
    route_outputs,
    staged_ref,
    submit_error,
    submit_failure,
)
from studio.registry.schema import Route

API = "https://api.dev.pika.art"
ERRORS = {
    "invalid_input": ErrorClass.invalid_request,
    "content_moderation": ErrorClass.moderation,
    "insufficient_balance": ErrorClass.route_unavailable,
    "membership_required": ErrorClass.route_unavailable,
    "cycle_limit_exceeded": ErrorClass.route_unavailable,
    "admission_suspended": ErrorClass.route_unavailable,
    "rate_limited": ErrorClass.transient,
    "provider_unavailable": ErrorClass.transient,
    "provider_timeout": ErrorClass.timeout,
    "timed_out": ErrorClass.timeout,
}


def spending_capacity(body: dict) -> tuple[int, bool]:
    """Amounts are micro-USD; invoice accounts spend against their cycle, not prepaid credits."""
    if body.get("unit") != "micro_usd" or body.get("currency") != "usd":
        raise ValueError("Unexpected Pika balance currency/unit")
    postpaid = body.get("postpaid") or {}
    invoiced = postpaid.get("active") is True
    amount = (postpaid.get("cycle") or {}).get("remaining_micro_usd") if invoiced else body.get("balance_micro_usd")
    if type(amount) is not int:
        raise ValueError("Missing Pika spending capacity")
    return amount, invoiced


class PikaAdapter(ProviderAdapter):
    key = "pika"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=API, timeout=httpx.Timeout(30, connect=10),
                                 headers={"X-API-Key": require_key("Pika", get_settings().pika_api_key)},
                                 transport=self._transport)

    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        async def upload(m: ResolvedMedia) -> tuple[str, datetime]:
            size = m.path.stat().st_size
            cap = 50 if m.kind == "video" else 15 if m.kind == "audio" else 20
            if size <= 0 or size > cap * 1024 * 1024:
                raise ProviderError(ErrorClass.invalid_request, f"Pika {m.kind} uploads must be 1 byte to {cap} MB")
            if m.kind == "audio" and m.mime not in ("audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"):
                raise ProviderError(ErrorClass.invalid_request, "Pika Seedance audio references must be WAV or MP3")
            if m.kind == "video" and m.mime not in ("video/mp4", "video/quicktime"):
                raise ProviderError(ErrorClass.invalid_request, "Pika Seedance video references must be MP4 or MOV")
            try:
                async with self._client() as c:
                    response = await c.post("/v1/media/uploads", json={"content_type": m.mime, "size_bytes": size})
                if response.status_code != 200:
                    raise http_error("Pika upload", response)
                data = response.json()
                if data.get("method", "PUT") != "PUT" or urlsplit(data["upload_url"]).scheme != "https":
                    raise ValueError("Invalid signed upload")

                async def chunks():
                    async with await anyio.open_file(m.path, "rb") as f:
                        while chunk := await f.read(1024 * 1024):
                            yield chunk

                # Separate client: never send the Pika key to signed storage URLs.
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as storage:
                    response = await storage.put(data["upload_url"], headers=data["headers"], content=chunks())
                if response.status_code not in (200, 201, 204):
                    raise ProviderError(ErrorClass.transient, f"Pika storage upload failed ({response.status_code})")
                if urlsplit(data["url"]).scheme != "https":
                    raise ValueError("Invalid uploaded URL")
                return data["url"], datetime.now(UTC) + timedelta(days=1)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise ProviderError(ErrorClass.transient, "Pika input upload failed before generation") from exc

        staged: dict[str, Any] = {}
        for m in media:
            staged.setdefault(m.role, []).append(await staged_ref(self.key, m, upload))
        return staged

    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        if endpoint not in {mode.endpoint for mode in route.modes.values()}:
            raise ProviderError(ErrorClass.invalid_request, "Pika endpoint is not registered on this route")
        body = dict(payload)
        try:
            ceiling = Decimal(body.pop("_studio_max_cost_usd")) * 1_000_000
            if not ceiling.is_finite() or ceiling <= 0:
                raise ValueError
            async with self._client() as c:
                response = await c.post(f"/catalog/apis/{endpoint}/quote", json=body)
                if response.status_code != 200:
                    raise http_error("Pika quote", response)
                quoted = response.json()["micro_usd"]
                if type(quoted) is not int or quoted < 0:
                    raise ValueError
                if quoted > ceiling:
                    raise ProviderError(ErrorClass.route_unavailable,
                                        "Pika quote exceeds the studio reservation; refresh route pricing before retrying")
                response = await c.get("/billing/balance")
                if response.status_code != 200:
                    raise http_error("Pika balance", response)
                available, _ = spending_capacity(response.json())
                if available < quoted:
                    raise ProviderError(ErrorClass.route_unavailable, "Pika balance or invoice allowance cannot cover this job")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
            raise ProviderError(ErrorClass.route_unavailable, "Pika quote/balance check failed; no generation submitted") from exc

        try:
            async with self._client() as c:
                response = await c.post(f"/v1/media/{endpoint}", json=body,
                                        headers={"Idempotency-Key": client_ref})
        except httpx.HTTPError as exc:
            raise submit_failure("Pika", exc) from exc
        # A rejected admission may still be a job (403/429/503). Poll it to obtain its settled charge.
        try:
            data = response.json()
            job_id = data.get("id")
            if isinstance(job_id, str) and job_id and data.get("status") in ("queued", "running", "completed", "failed"):
                return SubmitResult(job_id=job_id, raw={"endpoint": endpoint, "status": data["status"]})
        except (ValueError, AttributeError):
            pass
        if response.status_code not in (200, 201, 202):
            raise submit_error("Pika", response)
        raise AmbiguousSubmitError("Pika accepted the request but returned no valid job ID; do not resubmit")

    async def poll(self, route: Route, job_id: str) -> PollResult:
        # Wait briefly for terminal billing, within the worker's 60-second poll timeout. Never substitute $0
        # for an unsettled charge or generate another take because a completed result is temporarily unreadable.
        async with self._client() as c:
            for attempt in range(3):
                try:
                    response = await c.get(f"/v1/media/jobs/{quote(job_id, safe='')}", timeout=8)
                except httpx.HTTPError as exc:
                    raise ProviderError(ErrorClass.transient, "Pika status temporarily unreachable") from exc
                if response.status_code != 200:
                    raise http_error("Pika status", response)
                data = response.json()
                status = data.get("status")
                if status in ("queued", "running"):
                    return PollResult(status=status)
                if status not in ("completed", "failed") or data.get("deleted_at") is not None:
                    raise ProviderError(ErrorClass.route_unavailable, "Pika job is unreadable or its media was deleted")
                billing = data.get("billing") or {}
                if billing.get("state") == "settled":
                    break
                if attempt == 2 or (billing.get("state") == "unavailable" and attempt > 0):
                    raise ProviderError(ErrorClass.route_unavailable,
                                        "Pika job finished but billing is not settled; reconcile this job before retrying")
                await asyncio.sleep(30 if billing.get("state") == "unavailable" else attempt + 1)

        charge = billing.get("charge_micro_usd")
        if type(charge) is not int or charge < 0:
            raise ProviderError(ErrorClass.route_unavailable, "Pika returned an invalid settled charge")
        usage = {"cost_usd": charge / 1_000_000, "cost_estimated": False}
        if status == "failed":
            error = data.get("error") or {}
            code = error.get("code", "provider_error")
            return PollResult(status="failed", error_class=ERRORS.get(code, ErrorClass.internal),
                              error_message=f"Pika job failed: {code}", usage=usage)
        outputs = route_outputs(data, route.output)
        if not outputs:
            raise ProviderError(ErrorClass.route_unavailable, "Pika completed and may have billed, but its output is missing")
        return PollResult(status="succeeded", progress=1, outputs=outputs, usage=usage)

    async def cancel(self, route: Route, job_id: str) -> bool:
        # DELETE erases finished media and is explicitly NOT cancellation.
        return False

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        url = output.url or ""
        try:
            async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                for _ in range(6):
                    parsed = urlsplit(url)
                    if parsed.scheme != "https":
                        raise ValueError("Output must use HTTPS")
                    headers = {"X-API-Key": require_key("Pika", get_settings().pika_api_key)} \
                        if parsed.netloc == urlsplit(API).netloc else {}
                    async with c.stream("GET", url, headers=headers) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            url = urljoin(url, response.headers["location"])
                            continue  # rebuild headers, so a CDN redirect never receives the API key
                        response.raise_for_status()
                        async with await anyio.open_file(dest, "wb") as f:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                await f.write(chunk)
                        return output.mime or "video/mp4"
            raise ValueError("Too many output redirects")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderError(ErrorClass.ingest_error, "Pika output download failed; retry ingest, not generation") from exc
