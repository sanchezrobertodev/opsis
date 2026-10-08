"""Turn estimated charges into what the providers billed, from their billing APIs (reads only, free).

- fal reports each request's cost (`/v1/models/billing-events`, admin key); each one is matched to its generation
  attempt by request id (`provider_job_id` is `app::request_id`), including failed requests fal billed that the
  studio never charged.
- Our own RunPod endpoints (wan-core) are billed per worker second, idle tail and model loading included; RunPod
  reports that per endpoint per hour (`/v2/billing/serverless`). Once an hour has settled, its difference is spread
  over the charges made in it (by their size); billed time with no charge (an idle tail running into the next hour)
  is booked on its own.

Each correction is an `adjustment` ledger row (the bill minus what was charged, so it can be negative), dated like
the charge it corrects so the day and month totals land where the spend happened. One per attempt, and wan-core hours
are marked in `basis.bucket`, so running this again books nothing twice. Requests the studio didn't make (other
apps on the same key) are reported, never booked. Other providers keep their estimates: Google, ElevenLabs and
Replicate don't report a cost per request.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.db.models import Generation, GenerationAttempt, SpendLedger
from studio.domain.enums import SPENT, LedgerEntry
from studio.registry.loader import get_registry

log = logging.getLogger(__name__)

TIMEOUT = 20.0
SETTLE = timedelta(hours=2)  # RunPod's hourly figures are taken as final this long after the hour ends
CENT = Decimal("0.000001")


@dataclass
class Result:
    provider: str
    status: str  # ok, unavailable (the key can't read billing), error, skipped (no key)
    checked: int = 0  # charges (or hours) compared with the bill
    billed_usd: float = 0.0
    charged_usd: float = 0.0
    booked_usd: float = 0.0  # adjustments booked by this run
    outside_usd: float = 0.0  # billed for requests the studio didn't make (not booked)
    detail: str | None = None


def _money(x: float | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


async def reconcile(session: AsyncSession, *, days: int = 3, now: datetime | None = None,
                    transport: httpx.AsyncBaseTransport | None = None) -> list[Result]:
    """Compare the last `days` days with the providers' bills and book the differences. Caller commits."""
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    out: list[Result] = []
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as c:
        out.append(await _guarded("fal", _fal(session, c, since, now)))
        reg = get_registry()
        for spec in reg.providers.values():
            if spec.adapter == "runpod_comfy" and spec.endpoint_setting:
                out.append(await _guarded(spec.id, _runpod_endpoint(session, c, spec, since, now)))
    return out


async def _guarded(provider: str, work) -> Result:
    try:
        return await work
    except httpx.HTTPError as exc:
        log.warning("reconcile %s: %r", provider, exc)
        return Result(provider=provider, status="error", detail=f"couldn't read the bill: {type(exc).__name__}")


# ---------------------------------------------------------------- fal


async def _fal_events(c: httpx.AsyncClient, key: str, since: datetime, until: datetime) -> list[dict[str, Any]] | None:
    events: list[dict[str, Any]] = []
    params: dict[str, Any] = {"start": since.isoformat(), "end": until.isoformat(), "limit": 1000}
    while True:
        r = await c.get("https://api.fal.ai/v1/models/billing-events", params=params,
                        headers={"Authorization": f"Key {key}"})
        if r.status_code in (401, 403):
            return None
        r.raise_for_status()
        body = r.json()
        events += body.get("billing_events") or []
        if not body.get("next_cursor"):
            return events
        params["cursor"] = body["next_cursor"]


async def _fal(session: AsyncSession, c: httpx.AsyncClient, since: datetime, now: datetime) -> Result:
    key = get_settings().fal_key
    if not key:
        return Result(provider="fal", status="skipped", detail="no FAL_KEY")
    events = await _fal_events(c, key, since, now)
    if events is None:
        return Result(provider="fal", status="unavailable", detail="this key can't read billing (fal needs an admin key)")
    billed: dict[str, Decimal] = {}
    for e in events:
        billed[e["request_id"]] = billed.get(e["request_id"], Decimal(0)) + _money(e.get("cost_total") or 0)

    # attempts are made a little before fal bills them; look back a day further than the bill
    rows = (await session.execute(
        select(GenerationAttempt, Generation.user_id)
        .join(Generation, Generation.id == GenerationAttempt.generation_id)
        .where(GenerationAttempt.provider == "fal", GenerationAttempt.provider_job_id.is_not(None),
               GenerationAttempt.created_at >= since - timedelta(days=1)))).all()
    attempts = {a.provider_job_id.rsplit("::", 1)[-1]: (a, user) for a, user in rows}
    ledger = await _ledger_by_attempt(session, [a.id for a, _ in rows])

    res = Result(provider="fal", status="ok")
    for rid, usd in billed.items():
        if rid not in attempts:
            res.outside_usd += float(usd)
            continue
        attempt, user = attempts[rid]
        charged, adjusted, when = ledger.get(attempt.id, (Decimal(0), False, None))
        res.checked += 1
        res.billed_usd += float(usd)
        res.charged_usd += float(charged)
        if adjusted:
            continue
        delta = usd - charged
        session.add(SpendLedger(
            user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=delta, provider="fal",
            route_id=attempt.route_id, generation_attempt_id=attempt.id, estimated=False,
            created_at=when or attempt.created_at,
            basis={"source": "fal billing", "request_id": rid, "billed_usd": float(usd), "charged_usd": float(charged)}))
        res.booked_usd += float(delta)
    return res


async def _ledger_by_attempt(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal, bool, Any]]:
    """attempt -> (what was charged, whether it was already reconciled, when the charge was made)."""
    out: dict[uuid.UUID, tuple[Decimal, bool, Any]] = {}
    if not ids:
        return out
    rows = (await session.execute(select(SpendLedger).where(
        SpendLedger.generation_attempt_id.in_(ids), SpendLedger.entry_type.in_(SPENT)))).scalars().all()
    for r in rows:
        charged, adjusted, when = out.get(r.generation_attempt_id, (Decimal(0), False, None))
        if r.entry_type == LedgerEntry.adjustment:
            out[r.generation_attempt_id] = (charged, True, when)
        else:
            out[r.generation_attempt_id] = (charged + r.amount_usd, adjusted, r.created_at)
    return out


# ---------------------------------------------------------------- our RunPod endpoints


async def _runpod_endpoint(session: AsyncSession, c: httpx.AsyncClient, spec, since: datetime, now: datetime) -> Result:
    s = get_settings()
    endpoint = getattr(s, spec.endpoint_setting, "")
    if not (s.runpod_api_key and endpoint):
        return Result(provider=spec.id, status="skipped", detail="no RunPod key or endpoint id")
    until = (now - SETTLE).replace(minute=0, second=0, microsecond=0)
    res = Result(provider=spec.id, status="ok")
    if until <= since:
        return res
    r = await c.get("https://api.runpod.io/v2/billing/serverless", headers={"Authorization": f"Bearer {s.runpod_api_key}"},
                    params={"serverlessId": endpoint, "bucketSize": "hour", "startTime": since.isoformat(),
                            "endTime": until.isoformat()})
    if r.status_code in (401, 403):
        return Result(provider=spec.id, status="unavailable", detail="this key can't read billing")
    r.raise_for_status()
    billed = {datetime.fromisoformat(x["startTime"].replace("Z", "+00:00")): _money(x.get("totalAmount") or 0)
              for x in r.json().get("records") or []}

    charges = (await session.execute(select(SpendLedger).where(
        SpendLedger.provider == spec.id, SpendLedger.entry_type.in_(SPENT),
        SpendLedger.created_at >= since, SpendLedger.created_at < until))).scalars().all()
    done = {x.basis.get("bucket") for x in charges if x.entry_type == LedgerEntry.adjustment}
    by_hour: dict[datetime, list[SpendLedger]] = {}
    for x in charges:
        if x.entry_type == LedgerEntry.charge:
            by_hour.setdefault(x.created_at.replace(minute=0, second=0, microsecond=0), []).append(x)

    for hour in sorted(set(billed) | set(by_hour)):
        key = hour.isoformat()
        if key in done:
            continue
        bill, rows = billed.get(hour, Decimal(0)), by_hour.get(hour, [])
        charged = sum((x.amount_usd for x in rows), Decimal(0))
        res.checked += 1
        res.billed_usd += float(bill)
        res.charged_usd += float(charged)
        basis = {"source": "runpod billing", "bucket": key, "billed_usd": float(bill), "charged_usd": float(charged)}
        if rows:
            # spread the hour's difference over its charges by size; the last takes the rounding
            delta, left = bill - charged, bill - charged
            for i, x in enumerate(rows):
                share = left if i == len(rows) - 1 else _money(delta * x.amount_usd / charged if charged else delta / len(rows))
                left -= share
                session.add(SpendLedger(
                    user_id=x.user_id, entry_type=LedgerEntry.adjustment, amount_usd=share, provider=spec.id,
                    route_id=x.route_id, generation_attempt_id=x.generation_attempt_id, estimated=False,
                    created_at=x.created_at, basis=basis))
            res.booked_usd += float(delta)
        elif bill > 0:
            # billed with nothing charged in the hour: a worker idling on after a job, or loading
            user = (await session.execute(select(SpendLedger.user_id).where(
                SpendLedger.provider == spec.id, SpendLedger.entry_type == LedgerEntry.charge,
                SpendLedger.created_at < hour + timedelta(hours=1)).order_by(SpendLedger.created_at.desc())
                .limit(1))).scalar_one_or_none()
            if user is None:
                res.outside_usd += float(bill)
                continue
            session.add(SpendLedger(
                user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=bill, provider=spec.id,
                route_id=f"{spec.id} idle", estimated=False, created_at=hour,
                basis={**basis, "label": f"{spec.name}: workers idling or loading"}))
            res.booked_usd += float(bill)
    return res
