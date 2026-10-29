"""Removing what a user made: hidden from the studio and its files taken off disk. Spending records stay (they hang
off the generation's attempts, which are kept), and files a saved character or a project (a film, a 3D scene) still
uses are kept, so removing a take never breaks them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import anyio
from sqlalchemy import Text, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Asset, Element, ElementAsset, Project
from studio.storage.local import get_storage

PROJECT_NOUN = {"cinema": "the film", "jutsu3d": "the 3D scene"}


async def in_use(session: AsyncSession, user_id: uuid.UUID, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Which of these assets a live character or project still uses, and which one: {asset_id: "the character Mara"}."""
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
    # project documents name assets by id in many places (picked takes, frames, models, worlds): match the text
    wanted = {str(a): a for a in asset_ids}
    docs = (await session.execute(select(Project.kind, Project.title, Project.document.cast(Text)).where(
        Project.user_id == user_id, Project.deleted_at.is_(None),
        or_(*(Project.document.cast(Text).contains(s) for s in wanted))))).all()
    for kind, title, text in docs:
        for s, aid in wanted.items():
            if s in text:
                used.setdefault(aid, f"{PROJECT_NOUN.get(kind, 'the project')} {title}")
    return used


async def remove_assets(session: AsyncSession, user_id: uuid.UUID,
                        assets: list[Asset]) -> tuple[list[Asset], dict[uuid.UUID, str]]:
    """Mark the assets nothing else uses as deleted. Returns them, and what keeps the others."""
    kept = await in_use(session, user_id, [a.id for a in assets])
    removed = [a for a in assets if a.id not in kept]
    now = datetime.now(UTC)
    for a in removed:
        a.deleted_at = now
    return removed, kept


async def delete_files(user_id: uuid.UUID, assets: list[Asset]) -> None:
    """After the commit (a rolled-back delete keeps its files): every file of an asset lives in its own folder."""
    storage = get_storage()
    for a in assets:
        await anyio.to_thread.run_sync(storage.delete_prefix, f"u/{user_id}/a/{a.id}")
