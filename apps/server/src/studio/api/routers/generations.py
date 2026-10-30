from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    ConfirmationProblem,
    GenerationCreate,
    GenerationOut,
    GenerationPage,
    GenerationRemoved,
    ValidationProblem,
)
from studio.api.serializers import generation_out
from studio.db.models import Asset, Generation, PipelineRun, RunStatus, User
from studio.domain import fsm
from studio.domain.enums import ACTIVE_STATUSES, ErrorClass, GenStatus
from studio.domain.generations import CreateRequest, cancel_generation, create_generation, inflight_attempt
from studio.domain.removal import delete_files, remove_assets
from studio.registry.loader import get_registry
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
        Asset.generation_id.in_([g.id for g in gens]), Asset.user_id.in_({g.user_id for g in gens}),
        Asset.deleted_at.is_(None)))).scalars().all()
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
        direction=body.direction.model_dump(exclude_none=True) if body.direction else None,
        project_id=body.project_id, shot_key=body.shot_key,
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
    ops: str | None = Query(None, description="Comma-separated op tags (e.g. instruction_edit,upscale): only takes "
                                              "whose model mode does one of these"),
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> GenerationPage:
    stmt = select(Generation).where(Generation.user_id == user.id, Generation.deleted_at.is_(None))
    if status == "active":
        stmt = stmt.where(Generation.status.in_(list(ACTIVE_STATUSES)))
    elif status in ("succeeded", "failed"):
        stmt = stmt.where(Generation.status == GenStatus(status))
    if output_type:
        stmt = stmt.where(Generation.output_type == output_type)
    if ops:
        wanted = {o.strip() for o in ops.split(",") if o.strip()}
        pairs = [(spec.id, m.id) for spec in get_registry().models.values() for m in spec.modes if wanted & set(m.ops)]
        if not pairs:
            return GenerationPage(items=[], next_cursor=None)
        stmt = stmt.where(tuple_(Generation.model_id, Generation.mode).in_(pairs))
    if cursor:
        ts, gid = _parse_cursor(cursor)
        stmt = stmt.where(or_(Generation.created_at < ts, and_(Generation.created_at == ts, Generation.id < gid)))
    rows = (await session.execute(
        stmt.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(limit + 1))).scalars().all()
    page, more = rows[:limit], len(rows) > limit
    return GenerationPage(items=await _with_assets(session, list(page)),
                          next_cursor=_cursor(page[-1].created_at, page[-1].id) if more else None)


async def _get_owned(session: AsyncSession, user: User, gid: uuid.UUID, *, lock: bool = False,
                     deleted_ok: bool = False) -> Generation:
    gen = await session.get(Generation, gid, with_for_update=lock)
    if gen is None or gen.user_id != user.id or (gen.deleted_at is not None and not deleted_ok):
        raise HTTPException(404, "generation not found")
    return gen


@router.get("/{generation_id}", response_model=GenerationOut)
async def get_generation(
    generation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> GenerationOut:
    # still readable once deleted: outputs kept for a character or a project point back at it (a world's collider)
    gen = await _get_owned(session, user, generation_id, deleted_ok=True)
    return (await _with_assets(session, [gen]))[0]


@router.delete("/{generation_id}", response_model=GenerationRemoved)
async def delete_generation(
    generation_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> GenerationRemoved:
    """Remove a take from history and its files from disk. Spending records stay, and outputs a saved character or a
    project still uses are kept (the answer says which). A take already removed can be deleted again: what it left
    because something used it then goes once nothing does (a world whose 3D scene was deleted since)."""
    gen = await _get_owned(session, user, generation_id, lock=True, deleted_ok=True)
    if gen.status in ACTIVE_STATUSES:
        raise HTTPException(409, "This take is still rendering. Cancel it first, or mark it failed if it's stuck.")
    run = await session.get(PipelineRun, gen.group_id) if gen.group_id else None
    if run is not None and run.status in (RunStatus.running, RunStatus.waiting):
        raise HTTPException(409, "This take belongs to a run that is still going. Cancel the run or let it finish first.")
    outputs = (await session.execute(select(Asset).where(
        Asset.generation_id == gen.id, Asset.deleted_at.is_(None)).with_for_update())).scalars().all()
    removed, kept = await remove_assets(session, user.id, list(outputs))
    gen.deleted_at = gen.deleted_at or datetime.now(UTC)
    await fsm.emit(session, user_id=user.id, topic="generation", type="generation.deleted", subject_id=gen.id,
                   payload={"generation_id": str(gen.id)})
    await session.commit()
    await delete_files(user.id, removed)
    return GenerationRemoved(files_removed=len(removed), kept=sorted(set(kept.values())))


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
    """A submit whose outcome is unknown: the user checked the provider dashboard and marks it failed. Also a take
    canceled while it was being sent whose send ended with no answer: its held money is released."""
    from studio.budget import guard
    from studio.domain.enums import AttemptStatus

    gen = await _get_owned(session, user, generation_id, lock=True)
    attempt = await inflight_attempt(session, gen.id)
    held = gen.status == GenStatus.canceled and attempt is not None and attempt.status == AttemptStatus.unknown
    if gen.status != GenStatus.unknown and not held:
        raise HTTPException(409, "generation is not in the unknown state")
    if attempt is not None:
        attempt.status = AttemptStatus.failed
        await guard.settle(session, gen.user_id, attempt, None, estimated=True)
    if not held:
        await fsm.transition(session, gen, GenStatus.failed, reason="marked failed by user",
                             error=(ErrorClass.transient, gen.error_message or "submit outcome unknown"))
    await session.commit()
    return (await _with_assets(session, [gen]))[0]
