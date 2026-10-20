"""Turn estimated charges into what the providers billed, from their billing APIs (reads only, free).

- fal reports each request's cost (`/v1/models/billing-events`, admin key); each one is matched to its generation
  attempt by request id (`provider_job_id` is `app::request_id`), including failed requests fal billed that the
  studio never charged.
- Our own RunPod endpoints (wan-core) are billed per worker second, idle tail and model loading included; RunPod
  reports that per endpoint per hour (`/v2/billing/serverless`). Each settled hour's bill is shared by the jobs that
  ran in it, by how long (sent until finished plus the idle tail), so a job across an hour boundary gets a share of
  both; billed time with no job running is booked on its own.

Each correction is an `adjustment` ledger row (the bill minus what was charged, so it can be negative), dated like
the charge it corrects so the day and month totals land where the spend happened. One per attempt (a constraint), and
idle hours once per provider hour (a unique index); runs take an advisory lock, so the worker and the Settings button
never book the same bill twice. Requests the studio didn't make (other
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.db.models import Generation, GenerationAttempt, SpendLedger
from studio.domain.enums import SPENT, TERMINAL_STATUSES, LedgerEntry
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
    # one check at a time (the hourly worker and the Settings button share it), so a bill is never booked twice
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('studio.budget.reconcile'))"))
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
        select(GenerationAttempt, Generation.user_id, Generation.status)
        .join(Generation, Generation.id == GenerationAttempt.generation_id)
        .where(GenerationAttempt.provider == "fal", GenerationAttempt.provider_job_id.is_not(None),
               GenerationAttempt.created_at >= since - timedelta(days=1)))).all()
    attempts = {a.provider_job_id.rsplit("::", 1)[-1]: (a, user, status) for a, user, status in rows}
    ledger = await _ledger_by_attempt(session, [a.id for a, _, _ in rows])

    res = Result(provider="fal", status="ok")
    for rid, usd in billed.items():
        if rid not in attempts:
            res.outside_usd += float(usd)
            continue
        attempt, user, status = attempts[rid]
        if status not in TERMINAL_STATUSES:
            continue  # still running or being saved: its charge isn't booked yet, so compare it next time
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


def _overlap(a: datetime, b: datetime, start: datetime, end: datetime) -> float:
    return max(0.0, (min(b, end) - max(a, start)).total_seconds())


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
    billed = {h: usd for h, usd in billed.items() if since <= h < until}

    # Each job is billed from when it was sent (a cold worker loads while it waits) until its idle tail ends, so an
    # hour's bill is shared by the jobs that ran in it, by how long; a job across an hour boundary takes a share of
    # both. A job is corrected once, when every hour it touched has settled.
    idle = timedelta(seconds=spec.idle_seconds)
    rows = (await session.execute(
        select(GenerationAttempt, Generation.user_id)
        .join(Generation, Generation.id == GenerationAttempt.generation_id)
        .where(GenerationAttempt.provider == spec.id, GenerationAttempt.submitted_at.is_not(None),
               GenerationAttempt.finished_at.is_not(None), GenerationAttempt.submitted_at < until,
               GenerationAttempt.finished_at >= since - idle))).all()
    jobs = [(a, user, a.submitted_at, a.finished_at + idle) for a, user in rows]
    ledger = await _ledger_by_attempt(session, [a.id for a, *_ in jobs])
    idle_done = set((await session.execute(select(SpendLedger.basis["bucket"].astext).where(
        SpendLedger.provider == spec.id, SpendLedger.entry_type == LedgerEntry.adjustment,
        SpendLedger.generation_attempt_id.is_(None), SpendLedger.created_at >= since,
        SpendLedger.created_at < until))).scalars().all())

    share: dict[uuid.UUID, Decimal] = {}
    for hour, bill in sorted(billed.items()):
        end = hour + timedelta(hours=1)
        weights = {a.id: w for a, _, start, stop in jobs if (w := _overlap(start, stop, hour, end)) > 0}
        res.checked += 1
        res.billed_usd += float(bill)
        total = sum(weights.values())
        if total:
            for aid, w in weights.items():
                share[aid] = share.get(aid, Decimal(0)) + bill * Decimal(w) / Decimal(total)
        elif bill > 0 and hour.isoformat() not in idle_done:
            # billed with no job running: a worker idling on or loading. Booked to whoever last used the endpoint.
            user = next((u for a, u, start, _ in sorted(jobs, key=lambda j: j[2], reverse=True) if start < end), None)
            if user is None:
                res.outside_usd += float(bill)
                continue
            session.add(SpendLedger(
                user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=bill, provider=spec.id,
                route_id=f"{spec.id} idle", estimated=False, created_at=hour,
                basis={"source": "runpod billing", "bucket": hour.isoformat(), "billed_usd": float(bill),
                       "label": f"{spec.name}: workers idling or loading"}))
            res.booked_usd += float(bill)

    for a, user, start, stop in jobs:
        charged, adjusted, when = ledger.get(a.id, (Decimal(0), False, None))
        if adjusted or start < since or stop > until or a.id not in share:
            continue  # done before, or an hour it ran in is outside this window or not settled yet
        usd = _money(share[a.id])
        res.charged_usd += float(charged)
        session.add(SpendLedger(
            user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=usd - charged, provider=spec.id,
            route_id=a.route_id, generation_attempt_id=a.id, estimated=False, created_at=when or a.finished_at,
            basis={"source": "runpod billing", "billed_usd": float(usd), "charged_usd": float(charged),
                   "hours": sorted(h.isoformat() for h in billed if _overlap(start, stop, h, h + timedelta(hours=1)))}))
        res.booked_usd += float(usd - charged)
    return res
