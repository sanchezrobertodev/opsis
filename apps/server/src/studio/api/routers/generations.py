from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    ConfirmationProblem,
    GenerationCreate,
    GenerationOut,
    GenerationPage,
    ValidationProblem,
)
from studio.api.serializers import generation_out
from studio.db.models import Asset, Generation, User
from studio.domain import fsm
from studio.domain.enums import ACTIVE_STATUSES, ErrorClass, GenStatus
from studio.domain.generations import CreateRequest, cancel_generation, create_generation, inflight_attempt
from studio.worker.tasks.generation import defer_cancel_remote, defer_submit

router = APIRouter(prefix="/generations", tags=["generations"])


def _cursor(created_at: datetime, gid: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{gid}".encode()).decode()


def _parse_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, gid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(gid)
    except Exception:
        raise HTTPException(400, "bad cursor") from None


async def _with_assets(session: AsyncSession, gens: list[Generation]) -> list[GenerationOut]:
    if not gens:
        return []
    rows = (await session.execute(select(Asset).where(
        Asset.generation_id.in_([g.id for g in gens]), Asset.deleted_at.is_(None)))).scalars().all()
    by_gen: dict[uuid.UUID, list[Asset]] = {}
    for a in rows:
        by_gen.setdefault(a.generation_id, []).append(a)
    return [generation_out(g, by_gen.get(g.id, [])) for g in gens]


@router.post(
    "", response_model=GenerationOut, status_code=201,
    responses={409: {"model": ConfirmationProblem}, 422: {"model": ValidationProblem}},
)
async def create(
    body: GenerationCreate,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> GenerationOut:
    gen, created = await create_generation(session, user.id, CreateRequest(
        model_id=body.model_id, mode=body.mode, prompt=body.prompt, negative_prompt=body.negative_prompt,
        params=body.params, media=[{"role": m.role, "asset_id": str(m.asset_id)} for m in body.media],
        route_policy=body.route_policy, confirm_cost_usd=body.confirm_cost_usd,
        idempotency_key=idempotency_key, group_id=body.group_id, parent_generation_id=body.parent_generation_id,
    ))
    await session.commit()
    if created:
        # Committed first, then deferred: if we crash in between, the reaper re-defers within a minute.
        await defer_submit(gen.id)
    return (await _with_assets(session, [gen]))[0]


@router.get("", response_model=GenerationPage)
async def list_generations(
    status: Literal["active", "all", "succeeded", "failed"] = "all",
    output_type: Literal["video", "image", "audio"] | None = None,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> GenerationPage:
    stmt = select(Generation).where(Generation.user_id == user.id)
    if status == "active":
        stmt = stmt.where(Generation.status.in_(list(ACTIVE_STATUSES)))
    elif status in ("succeeded", "failed"):
        stmt = stmt.where(Generation.status == GenStatus(status))
    if output_type:
        stmt = stmt.where(Generation.output_type == output_type)
    if cursor:
        ts, gid = _parse_cursor(cursor)
        stmt = stmt.where(or_(Generation.created_at < ts, and_(Generation.created_at == ts, Generation.id < gid)))
    rows = (await session.execute(
        stmt.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(limit + 1))).scalars().all()
    page, more = rows[:limit], len(rows) > limit
    return GenerationPage(items=await _with_assets(session, list(page)),
                          next_cursor=_cursor(page[-1].created_at, page[-1].id) if more else None)


async def _get_owned(session: AsyncSession, user: User, gid: uuid.UUID, *, lock: bool = False) -> Generation:
    gen = await session.get(Generation, gid, with_for_update=lock)
    if gen is None or gen.user_id != user.id:
        raise HTTPException(404, "generation not found")
    return gen


@router.get("/{generation_id}", response_model=GenerationOut)
async def get_generation(
    generation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> GenerationOut:
    gen = await _get_owned(session, user, generation_id)
    return (await _with_assets(session, [gen]))[0]


@router.post("/{generation_id}/cancel", response_model=GenerationOut)
async def cancel(
    generation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> GenerationOut:
    gen = await _get_owned(session, user, generation_id, lock=True)
    try:
        remote = await cancel_generation(session, gen)
    except fsm.IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    if remote is not None:
        await defer_cancel_remote(remote.id)
    return (await _with_assets(session, [gen]))[0]


@router.post("/{generation_id}/resolve-unknown", response_model=GenerationOut)
async def resolve_unknown(
    generation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> GenerationOut:
    """A submit whose outcome is unknown: the user checked the provider dashboard and marks it failed."""
    gen = await _get_owned(session, user, generation_id, lock=True)
    if gen.status != GenStatus.unknown:
        raise HTTPException(409, "generation is not in the unknown state")
    attempt = await inflight_attempt(session, gen.id)
    if attempt is not None:
        from studio.budget import guard
        from studio.domain.enums import AttemptStatus

        attempt.status = AttemptStatus.failed
        await guard.settle(session, gen.user_id, attempt, None, estimated=True)
    await fsm.transition(session, gen, GenStatus.failed, reason="marked failed by user",
                         error=(ErrorClass.transient, gen.error_message or "submit outcome unknown"))
    await session.commit()
    return (await _with_assets(session, [gen]))[0]
