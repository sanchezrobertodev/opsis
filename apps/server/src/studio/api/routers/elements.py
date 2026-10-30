"""Characters, locations, props and styles (Elements). Mention one as `@handle` in any prompt."""

from __future__ import annotations

import shutil
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
from studio.budget import modes
from studio.config import get_settings
from studio.db.models import Asset, Element, ElementAsset, ElementKind, User
from studio.director.elements import unique_slug
from studio.domain.enums import AssetKind
from studio.media.ffmpeg import ffmpeg
from studio.providers import elevenlabs
from studio.providers.base import ProviderError
from studio.registry.loader import get_registry
from studio.storage.local import get_storage

router = APIRouter(prefix="/elements", tags=["elements"])

Kind = Literal["character", "location", "prop", "style", "voice"]
View = Literal["front", "three_quarter", "side", "back", "face", "full_body", "sheet", "wide", "detail", "style"]


class ElementImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: uuid.UUID
    view: View = "front"


class VoiceIn(BaseModel):
    """The voice a character speaks with: a text-to-speech model and its params (e.g. voice_id)."""

    model_config = ConfigDict(extra="forbid")
    model_id: str = "eleven-v4"
    params: dict[str, Any] = {}


def _check_voice(voice: VoiceIn) -> dict[str, Any]:
    from studio.pipelines.recipes.common import mode_with_op
    from studio.registry.validate import RequestError, normalize

    spec = get_registry().model(voice.model_id)
    mode = mode_with_op(spec, "tts") if spec else None
    if spec is None or mode is None:
        raise HTTPException(422, f"{voice.model_id!r} can't speak")
    try:
        req = normalize(spec, mode.id, "voice check", voice.params, [])
    except RequestError as exc:
        raise HTTPException(422, "; ".join(exc.errors)) from None
    if unknown := [w for w in req.warnings if "unknown parameter" in w]:
        raise HTTPException(422, "; ".join(unknown))
    return {"model_id": spec.id, "params": {k: v for k, v in voice.params.items() if v is not None}}


class ElementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = "character"
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=600)
    attributes: dict[str, Any] = {}
    images: list[ElementImageIn] = []
    voice: VoiceIn | None = None


class ElementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind | None = None
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=600)
    attributes: dict[str, Any] | None = None
    cover_asset_id: uuid.UUID | None = None
    voice: VoiceIn | None = Field(None, description="Set the voice; `clear_voice` removes it")
    clear_voice: bool = False
    clear_lora: bool = Field(False, description="Forget the trained identity (the LoRA files stay in the library)")


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
    voice: dict[str, Any] | None = Field(None, description="{model_id, params}: how this character speaks")
    lora: dict[str, Any] | None = Field(None, description="The trained identity: {base, trigger, low, high, steps, run_id, trained_at}; models with the lora strategy load it when the character is mentioned")
    cover: AssetOut | None
    images: list[ElementImageOut]
    created_at: datetime


async def _owned_assets(session: AsyncSession, user: User, ids: list[uuid.UUID],
                        images: bool = True) -> dict[uuid.UUID, Asset]:
    # FOR SHARE: a delete waits for this save, or this save sees the asset gone (domain/removal.py)
    rows = (await session.execute(select(Asset).where(
        Asset.id.in_(ids), Asset.user_id == user.id, Asset.deleted_at.is_(None)).with_for_update(read=True))).scalars().all()
    found = {a.id: a for a in rows}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HTTPException(422, f"assets not found: {', '.join(missing)}")
    bad = [str(a.id) for a in rows if a.kind.value != "image"]
    if images and bad:
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
                      description=e.description, attributes=e.attributes or {}, voice=e.voice or None, lora=e.lora or None,
                      cover=cover, images=images, created_at=e.created_at)


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
                slug=await unique_slug(session, user.id, body.name), description=body.description.strip(),
                attributes=body.attributes, voice=_check_voice(body.voice) if body.voice else {})
    session.add(e)
    await session.flush()
    for n, img in enumerate(body.images):
        session.add(ElementAsset(element_id=e.id, asset_id=assets[img.asset_id].id, view=img.view,
                                 is_primary=n == 0, sort=n))
    if body.images:
        e.cover_asset_id = body.images[0].asset_id
    await session.commit()
    return await _out(session, e)


SAMPLE_MAX_BYTES = 25 << 20
SAMPLES_MAX_BYTES = 50 << 20


class CloneVoiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    samples: list[uuid.UUID] = Field(min_length=1, max_length=5, description="Recordings of the voice (audio or clips)")
    remove_background_noise: bool = False
    consent: Literal[True] = Field(description="The speaker agreed to having their voice cloned")


@router.post("/clone-voice", response_model=ElementOut, status_code=201,
             responses={502: {"description": "The voice service refused or failed"}})
async def clone_voice(body: CloneVoiceIn, session: AsyncSession = Depends(get_session),
                      user: User = Depends(current_user)) -> ElementOut:
    """Make a voice from recordings (an ElevenLabs instant clone) and keep it as a voice you can pick anywhere.
    In mock mode nothing is sent and a stand-in voice is made."""
    assets = await _owned_assets(session, user, body.samples, images=False)
    sounds = [a for a in assets.values() if a.kind in (AssetKind.audio, AssetKind.video) and a.has_audio is not False]
    if len(sounds) != len(body.samples):
        raise HTTPException(422, "every sample must be a recording or a clip with sound")
    if any((a.size_bytes or 0) > SAMPLE_MAX_BYTES for a in sounds) or \
            sum(a.size_bytes or 0 for a in sounds) > SAMPLES_MAX_BYTES:
        raise HTTPException(422, f"keep each sample under {SAMPLE_MAX_BYTES >> 20} MB and all of them under "
                                 f"{SAMPLES_MAX_BYTES >> 20} MB; a minute or two of clear speech is plenty")
    mode = (await modes.current(session, user.id)).provider
    live = mode == "live" or (mode == "mixed" and any(r.endswith("@elevenlabs") for r in get_settings().live_routes))
    if not live:  # mock, or mixed without an ElevenLabs route allowed: nothing leaves the studio
        voice_id = f"mock-voice-{uuid.uuid4().hex[:12]}"
    else:
        storage = get_storage()
        work = storage.temp_dir() / f"clone-{uuid.uuid4().hex}"
        work.mkdir(parents=True)
        try:
            files = []
            for i, a in enumerate(sounds):
                path = storage.path_for(a.storage_key)
                if a.kind == AssetKind.video:  # the voice service takes audio: the clip's sound
                    path = work / f"sample_{i}.mp3"
                    await ffmpeg("-i", str(storage.path_for(a.storage_key)), "-vn", "-c:a", "libmp3lame", str(path))
                files.append(path)
            voice_id = await elevenlabs.clone_voice(body.name.strip(), files,
                                                    remove_background_noise=body.remove_background_noise)
        except ProviderError as exc:
            raise HTTPException(502, f"ElevenLabs couldn't clone the voice: {exc.message}") from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
    e = Element(user_id=user.id, kind=ElementKind.voice, name=body.name.strip(),
                slug=await unique_slug(session, user.id, body.name), description="",
                attributes={"samples": [str(a.id) for a in sounds]},
                voice={"model_id": "eleven-v4", "params": {"voice_id": voice_id}},
                cloned_voice_id=voice_id if live else None)
    session.add(e)
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
        e.slug = await unique_slug(session, user.id, e.name, exclude=e.id)
    if body.kind is not None:
        e.kind = ElementKind(body.kind)
    if body.description is not None:
        e.description = body.description.strip()
    if body.attributes is not None:
        e.attributes = body.attributes
    if body.cover_asset_id is not None:
        await _owned_assets(session, user, [body.cover_asset_id])  # only a picture of yours can be the cover
        e.cover_asset_id = body.cover_asset_id
    if body.voice is not None:
        e.voice = _check_voice(body.voice)
    elif body.clear_voice:
        e.voice = {}
    if body.clear_lora:
        e.lora = {}
    await session.commit()
    return await _out(session, e)


@router.delete("/{element_id}", status_code=204)
async def delete_element(element_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> None:
    e = await _get(session, user, element_id, lock=True)
    if e.cloned_voice_id:  # only a voice the clone endpoint made, never an id typed into the element
        try:  # free the account's voice slot; keep ours if that fails, so the slot isn't lost track of
            await elevenlabs.delete_voice(e.cloned_voice_id)
        except ProviderError as exc:
            raise HTTPException(502, f"ElevenLabs couldn't remove the voice: {exc.message}") from exc
    e.deleted_at = datetime.now(UTC)
    e.slug = f"{e.slug[:29]}-del-{uuid.uuid4().hex[:6]}"  # frees the handle for reuse; fits slug's 40 characters
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
