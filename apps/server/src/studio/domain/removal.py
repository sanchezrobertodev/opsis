"""Removing what a user made: hidden from the studio and its files taken off disk. Spending records stay (they hang
off the generation's attempts, which are kept), and files that something still uses are kept, so removing a take never
breaks it: a saved character, a project (a film, a 3D scene), a take still rendering or a run still going.

Races with a save that adds a reference: a delete locks the assets FOR UPDATE before checking, and every writer that
takes an asset in (project saves, characters' pictures, takes' media, run inputs) checks it FOR SHARE. So the delete
waits for that save to commit and then sees the reference, or the save waits for the delete and finds it gone.

Files go after the commit and are flagged until they're gone (`purge_pending`), so a file another program holds open
is retried by the reaper instead of being left behind unseen."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy import ColumnElement, Text, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import (
    Asset,
    Element,
    ElementAsset,
    Generation,
    PipelineRun,
    PipelineStep,
    Project,
    RunStatus,
)
from studio.db.session import session_scope
from studio.domain.enums import ACTIVE_STATUSES
from studio.storage.local import get_storage

log = logging.getLogger(__name__)

PURGE = "purge_pending"
PROJECT_NOUN = {"cinema": "the film", "jutsu3d": "the 3D scene"}


async def in_use(session: AsyncSession, user_id: uuid.UUID, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Which of these assets something still uses, and what: {asset_id: "the character Mara"}."""
    if not asset_ids:
        return {}
    used: dict[uuid.UUID, str] = {}
    pictures = await session.execute(
        select(ElementAsset.asset_id, Element.name).join(Element, Element.id == ElementAsset.element_id)
        .where(ElementAsset.asset_id.in_(asset_ids), Element.user_id == user_id, Element.deleted_at.is_(None)))
    covers = await session.execute(select(Element.cover_asset_id, Element.name).where(
        Element.cover_asset_id.in_(asset_ids), Element.user_id == user_id, Element.deleted_at.is_(None)))
    for aid, name in [*pictures, *covers]:
        used.setdefault(aid, f"the character {name}")
    # project documents, requests and run inputs name assets by id in many places: match the text
    wanted = {str(a): a for a in asset_ids}

    def mentions(column) -> ColumnElement[bool]:
        return or_(*(column.cast(Text).contains(s) for s in wanted))

    docs = await session.execute(select(Project.kind, Project.title, Project.document.cast(Text)).where(
        Project.user_id == user_id, Project.deleted_at.is_(None), mentions(Project.document)))
    texts = [(f"{PROJECT_NOUN.get(kind, 'the project')} {title}", text) for kind, title, text in docs]
    # a take reads its inputs when it's submitted; a run reads its input and earlier steps' outputs as it goes
    rendering = await session.execute(select(Generation.request.cast(Text)).where(
        Generation.user_id == user_id, Generation.status.in_(ACTIVE_STATUSES), mentions(Generation.request)))
    texts += [("a take still rendering", text) for text in rendering.scalars()]
    going = (PipelineRun.user_id == user_id) & PipelineRun.status.in_((RunStatus.running, RunStatus.waiting))
    # a run's take can finish before its step takes the output in, so what it made is the run's until the run ends
    made = await session.execute(select(Asset.id).join(Generation, Generation.id == Asset.generation_id)
                                 .join(PipelineRun, PipelineRun.id == Generation.group_id)
                                 .where(Asset.id.in_(asset_ids), going))
    for aid in made.scalars():
        used.setdefault(aid, "a run still going")
    runs = await session.execute(select(PipelineRun.input.cast(Text)).where(going, mentions(PipelineRun.input)))
    steps = await session.execute(select(PipelineStep.output.cast(Text)).join(
        PipelineRun, PipelineRun.id == PipelineStep.run_id).where(going, mentions(PipelineStep.output)))
    texts += [("a run still going", text) for text in [*runs.scalars(), *steps.scalars()]]
    for label, text in texts:
        for s, aid in wanted.items():
            if s in text:
                used.setdefault(aid, label)
    return used


async def remove_assets(session: AsyncSession, user_id: uuid.UUID,
                        assets: list[Asset]) -> tuple[list[Asset], dict[uuid.UUID, str]]:
    """Mark the assets nothing else uses as deleted. Returns them, and what keeps the others. The assets must be
    locked FOR UPDATE by the caller (see the module docstring)."""
    kept = await in_use(session, user_id, [a.id for a in assets])
    removed = [a for a in assets if a.id not in kept]
    now = datetime.now(UTC)
    for a in removed:
        a.deleted_at = now
        a.metadata_ = {**(a.metadata_ or {}), PURGE: True}
    return removed, kept


async def delete_files(user_id: uuid.UUID, assets: list[Asset]) -> int:
    """After the commit (a rolled-back delete keeps its files): every file of an asset lives in its own folder.
    Returns how many folders are still there (a file held open on Windows); those stay flagged for the reaper."""
    storage = get_storage()
    gone = []
    for a in assets:
        key = f"u/{user_id}/a/{a.id}"
        await anyio.to_thread.run_sync(storage.delete_prefix, key)
        if storage.path_for(key).exists():
            log.warning("could not delete every file of asset %s; the reaper will try again", a.id)
        else:
            gone.append(a.id)
    if gone:
        async with session_scope() as s:
            for a in (await s.execute(select(Asset).where(Asset.id.in_(gone)))).scalars():
                a.metadata_ = {k: v for k, v in (a.metadata_ or {}).items() if k != PURGE}
    return len(assets) - len(gone)


async def retry_purges() -> int:
    """The reaper's retry for files a delete couldn't remove. Returns how many are still left."""
    async with session_scope() as s:
        pending = (await s.execute(select(Asset).where(
            Asset.deleted_at.is_not(None), Asset.metadata_.has_key(PURGE)).limit(500))).scalars().all()
    left = 0
    for user_id in {a.user_id for a in pending}:
        left += await delete_files(user_id, [a for a in pending if a.user_id == user_id])
    return left
