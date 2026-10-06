"""Media serving, live events, budget and health."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    BudgetOut,
    BudgetUpdate,
    HealthOut,
    LocalEngineOut,
    SpendBreakdown,
    SpendEntryOut,
    SpendHistoryOut,
)
from studio.api.sse import hub
from studio.budget import guard
from studio.config import get_settings
from studio.db.models import Generation, GenerationAttempt, JobEvent, SpendLedger, User
from studio.domain.enums import LedgerEntry
from studio.providers.local_status import comfy_stats
from studio.registry.loader import get_registry_holder
from studio.storage.local import get_storage

router = APIRouter()

REPLAY_LIMIT = 500


@router.get("/media/{key:path}", tags=["media"], include_in_schema=False)
async def media(key: str, user: User = Depends(current_user)) -> FileResponse:
    """Serve stored media with HTTP Range support (video seeking). Keys are scoped to the owner."""
    if not key.startswith(f"u/{user.id}/"):
        raise HTTPException(404, "not found")
    try:
        path = get_storage().path_for(key)
    except ValueError:
        raise HTTPException(404, "not found") from None
    if not path.is_file():
        raise HTTPException(404, "not found")
    # Content is immutable per key, so browsers may cache aggressively.
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/events/stream", tags=["events"])
async def events(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Server-sent events. Each message's data is JSON {id, topic, type, subject_id, payload}."""
    sub = hub.subscribe(user.id)
    replay: list[dict] = []
    if last_event_id and last_event_id.isdigit():
        rows = (await session.execute(
            select(JobEvent).where(JobEvent.user_id == user.id, JobEvent.id > int(last_event_id))
            .order_by(JobEvent.id).limit(REPLAY_LIMIT))).scalars().all()
        replay = [{"id": r.id, "user_id": str(r.user_id), "topic": r.topic, "type": r.type,
                   "subject_id": str(r.subject_id) if r.subject_id else None, "payload": r.payload} for r in rows]
    await session.close()  # don't hold a DB connection for the life of the stream

    async def stream():
        try:
            for msg in replay:
                yield {"id": str(msg["id"]), "data": json.dumps(msg, default=str)}
            yield {"data": json.dumps({"topic": "system", "type": "system.connected", "payload": {}})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=15)
                except TimeoutError:
                    continue
                event = {"data": json.dumps(msg, default=str)}
                if msg.get("id") is not None:
                    event["id"] = str(msg["id"])
                yield event
        finally:
            hub.unsubscribe(sub)

    return EventSourceResponse(stream(), ping=15)


def _budget_out(s: guard.SpendSummary) -> BudgetOut:
    return BudgetOut(
        today_usd=float(s.today_usd), month_usd=float(s.month_usd), reserved_usd=float(s.reserved_usd),
        daily_cap_usd=float(s.daily_cap_usd), monthly_cap_usd=float(s.monthly_cap_usd),
        confirm_over_usd=float(s.confirm_over_usd), live_enabled=s.live_enabled,
    )


@router.get("/budget", response_model=BudgetOut, tags=["budget"])
async def get_budget(session: AsyncSession = Depends(get_session), user: User = Depends(current_user)) -> BudgetOut:
    out = _budget_out(await guard.summary(session, user.id))
    await session.commit()
    return out


@router.patch("/budget", response_model=BudgetOut, tags=["budget"])
async def patch_budget(body: BudgetUpdate, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> BudgetOut:
    policy = await guard.get_policy(session, user.id, lock=True)
    for field in ("daily_cap_usd", "monthly_cap_usd", "confirm_over_usd"):
        value = getattr(body, field)
        if value is not None:
            setattr(policy, field, Decimal(str(value)))
    if body.live_enabled is not None:
        policy.live_enabled = body.live_enabled
    await session.flush()
    out = _budget_out(await guard.summary(session, user.id))
    await session.commit()
    return out


@router.get("/budget/history", response_model=SpendHistoryOut, tags=["budget"])
async def spend_history(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SpendHistoryOut:
    """Charges and refunds, newest first, with what each paid for."""
    rows = (await session.execute(
        select(SpendLedger, Generation.id, Generation.model_id, Generation.request)
        .outerjoin(GenerationAttempt, GenerationAttempt.id == SpendLedger.generation_attempt_id)
        .outerjoin(Generation, Generation.id == GenerationAttempt.generation_id)
        .where(SpendLedger.user_id == user.id,
               SpendLedger.entry_type.in_([LedgerEntry.charge, LedgerEntry.refund, LedgerEntry.adjustment]))
        .order_by(SpendLedger.created_at.desc()).limit(limit)
    )).all()
    entries = []
    for e, gen_id, model_id, request in rows:
        if gen_id is not None:
            kind, label = "generation", str((request or {}).get("prompt") or "Untitled take")
        elif e.provider == "anthropic":
            kind, label = "claude", f"Claude: {(e.basis or {}).get('purpose', 'request')}"
        else:
            kind, label = "other", e.route_id or e.entry_type.value
        entries.append(SpendEntryOut(
            id=e.id, created_at=e.created_at, amount_usd=float(e.amount_usd), entry_type=e.entry_type.value,
            provider=e.provider, route_id=e.route_id, estimated=e.estimated, kind=kind, label=label[:160],
            model_id=model_id, generation_id=gen_id,
        ))
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    totals = (await session.execute(
        select(SpendLedger.provider, func.sum(SpendLedger.amount_usd), func.count())
        .where(SpendLedger.user_id == user.id, SpendLedger.entry_type == LedgerEntry.charge,
               SpendLedger.created_at >= month)
        .group_by(SpendLedger.provider).order_by(func.sum(SpendLedger.amount_usd).desc())
    )).all()
    return SpendHistoryOut(entries=entries, by_provider=[
        SpendBreakdown(provider=p or "unknown", usd=float(usd), count=n) for p, usd, n in totals])


@router.get("/health", response_model=HealthOut, tags=["health"])
async def health(session: AsyncSession = Depends(get_session)) -> HealthOut:
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    holder = get_registry_holder()
    ff = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    url = get_settings().comfyui_url
    stats = await comfy_stats(url) if url else None
    device = ((stats or {}).get("devices") or [{}])[0]
    gib = 1024**3
    local = LocalEngineOut(
        configured=bool(url), online=stats is not None, url=url or None,
        gpu=device.get("name"),
        vram_total_gb=round(device["vram_total"] / gib, 1) if device.get("vram_total") else None,
        vram_free_gb=round(device["vram_free"] / gib, 1) if device.get("vram_free") else None,
        comfyui_version=((stats or {}).get("system") or {}).get("comfyui_version"),
    )
    return HealthOut(
        status="ok" if db_ok and ff and not holder.last_error else "degraded",
        database=db_ok, provider_mode=get_settings().provider_mode, registry_hash=holder.current.hash,
        registry_errors=holder.last_error, ffmpeg=ff, media_root=str(get_storage().root), local_engine=local,
        llm_mode=get_settings().llm_mode, llm_key_set=bool(get_settings().anthropic_api_key),
    )
