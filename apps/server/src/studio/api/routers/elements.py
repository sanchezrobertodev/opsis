"""Characters, locations, props and styles (Elements). Mention one as `@handle` in any prompt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import AssetOut
from studio.api.serializers import asset_out
from studio.db.models import Asset, Element, ElementAsset, ElementKind, User
from studio.director.elements import slugify

router = APIRouter(prefix="/elements", tags=["elements"])

Kind = Literal["character", "location", "prop", "style", "voice"]
View = Literal["front", "three_quarter", "side", "back", "face", "full_body", "sheet", "wide", "detail", "style"]


class ElementImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: uuid.UUID
    view: View = "front"


class ElementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = "character"
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=600)
    attributes: dict[str, Any] = {}
    images: list[ElementImageIn] = []


class ElementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind | None = None
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=600)
    attributes: dict[str, Any] | None = None
    cover_asset_id: uuid.UUID | None = None


class ElementImageOut(BaseModel):
    asset: AssetOut
    view: str
    is_primary: bool


class ElementOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    name: str
    slug: str
    handle: str
    description: str
    attributes: dict[str, Any]
    cover: AssetOut | None
    images: list[ElementImageOut]
    created_at: datetime


async def _unique_slug(session: AsyncSession, user_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> str:
    base = slugify(name)
    taken = set((await session.execute(select(Element.slug).where(
        Element.user_id == user_id, Element.slug.like(f"{base}%"),
        *( [Element.id != exclude] if exclude else []),
    ))).scalars())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}"[:40] in taken:
        n += 1
    return f"{base}-{n}"[:40]


async def _owned_assets(session: AsyncSession, user: User, ids: list[uuid.UUID]) -> dict[uuid.UUID, Asset]:
    rows = (await session.execute(select(Asset).where(
        Asset.id.in_(ids), Asset.user_id == user.id, Asset.deleted_at.is_(None)))).scalars().all()
    found = {a.id: a for a in rows}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HTTPException(422, f"assets not found: {', '.join(missing)}")
    bad = [str(a.id) for a in rows if a.kind.value != "image"]
    if bad:
        raise HTTPException(422, "reference images must be images")
    return found


async def _out(session: AsyncSession, e: Element) -> ElementOut:
    links = (await session.execute(
        select(ElementAsset, Asset).join(Asset, Asset.id == ElementAsset.asset_id)
        .where(ElementAsset.element_id == e.id, Asset.deleted_at.is_(None))
        .order_by(ElementAsset.is_primary.desc(), ElementAsset.sort)
    )).all()
    images = [ElementImageOut(asset=asset_out(a), view=link.view, is_primary=link.is_primary) for link, a in links]
    cover = next((i.asset for i in images if str(i.asset.id) == str(e.cover_asset_id)), images[0].asset if images else None)
    return ElementOut(id=e.id, kind=e.kind.value, name=e.name, slug=e.slug, handle=f"@{e.slug}",
                      description=e.description, attributes=e.attributes or {}, cover=cover, images=images,
                      created_at=e.created_at)


async def _get(session: AsyncSession, user: User, element_id: uuid.UUID, lock: bool = False) -> Element:
    e = await session.get(Element, element_id, with_for_update=lock)
    if e is None or e.user_id != user.id or e.deleted_at is not None:
        raise HTTPException(404, "element not found")
    return e


@router.get("", response_model=list[ElementOut])
async def list_elements(kind: Kind | None = None, q: str | None = Query(None, max_length=60),
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> list[ElementOut]:
    stmt = select(Element).where(Element.user_id == user.id, Element.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(Element.kind == ElementKind(kind))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(Element.name).like(like) | Element.slug.like(like))
    rows = (await session.execute(stmt.order_by(Element.updated_at.desc()).limit(200))).scalars().all()
    return [await _out(session, e) for e in rows]


@router.post("", response_model=ElementOut, status_code=201)
async def create_element(body: ElementCreate, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> ElementOut:
    assets = await _owned_assets(session, user, [i.asset_id for i in body.images])
    e = Element(user_id=user.id, kind=ElementKind(body.kind), name=body.name.strip(),
                slug=await _unique_slug(session, user.id, body.name), description=body.description.strip(),
                attributes=body.attributes)
    session.add(e)
    await session.flush()
    for n, img in enumerate(body.images):
        session.add(ElementAsset(element_id=e.id, asset_id=assets[img.asset_id].id, view=img.view,
                                 is_primary=n == 0, sort=n))
    if body.images:
        e.cover_asset_id = body.images[0].asset_id
    await session.commit()
    return await _out(session, e)


@router.get("/{element_id}", response_model=ElementOut)
async def get_element(element_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                      user: User = Depends(current_user)) -> ElementOut:
    return await _out(session, await _get(session, user, element_id))


@router.patch("/{element_id}", response_model=ElementOut)
async def patch_element(element_id: uuid.UUID, body: ElementPatch, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> ElementOut:
    e = await _get(session, user, element_id, lock=True)
    if body.name is not None and body.name.strip() != e.name:
        e.name = body.name.strip()
        e.slug = await _unique_slug(session, user.id, e.name, exclude=e.id)
    if body.kind is not None:
        e.kind = ElementKind(body.kind)
    if body.description is not None:
        e.description = body.description.strip()
    if body.attributes is not None:
        e.attributes = body.attributes
    if body.cover_asset_id is not None:
        e.cover_asset_id = body.cover_asset_id
    await session.commit()
    return await _out(session, e)


@router.delete("/{element_id}", status_code=204)
async def delete_element(element_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> None:
    e = await _get(session, user, element_id, lock=True)
    e.deleted_at = datetime.now(UTC)
    e.slug = f"{e.slug[:30]}-del-{uuid.uuid4().hex[:6]}"  # frees the handle for reuse
    await session.commit()


@router.post("/{element_id}/images", response_model=ElementOut)
async def add_images(element_id: uuid.UUID, body: list[ElementImageIn], session: AsyncSession = Depends(get_session),
                     user: User = Depends(current_user)) -> ElementOut:
    e = await _get(session, user, element_id, lock=True)
    await _owned_assets(session, user, [i.asset_id for i in body])
    existing = set((await session.execute(select(ElementAsset.asset_id).where(
        ElementAsset.element_id == e.id))).scalars())
    count = len(existing)
    for img in body:
        if img.asset_id in existing:
            continue
        session.add(ElementAsset(element_id=e.id, asset_id=img.asset_id, view=img.view, is_primary=count == 0,
                                 sort=count))
        count += 1
    if e.cover_asset_id is None and body:
        e.cover_asset_id = body[0].asset_id
    e.updated_at = datetime.now(UTC)
    await session.commit()
    return await _out(session, e)


@router.delete("/{element_id}/images/{asset_id}", response_model=ElementOut)
async def remove_image(element_id: uuid.UUID, asset_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> ElementOut:
    e = await _get(session, user, element_id, lock=True)
    link = await session.get(ElementAsset, (e.id, asset_id))
    if link is None:
        raise HTTPException(404, "image not attached to this element")
    await session.delete(link)
    if e.cover_asset_id == asset_id:
        e.cover_asset_id = None
    await session.commit()
    return await _out(session, e)
