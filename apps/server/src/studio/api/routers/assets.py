from __future__ import annotations

import base64
import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import AssetLineageOut, AssetOut, AssetPage, AssetPatch, LineageEdge
from studio.api.serializers import asset_out
from studio.db.base import uuid7
from studio.db.models import Asset, AssetLineage, User
from studio.domain import fsm
from studio.domain.enums import AssetKind, AssetSource, AssetStatus
from studio.media.ffmpeg import FFmpegError, probe
from studio.storage.base import asset_key
from studio.storage.local import get_storage
from studio.worker.tasks.assets import defer_derive

router = APIRouter(tags=["assets"])

MAX_UPLOAD_BYTES = 4 * 1024**3
CHUNK = 1 << 20


def _kind_for(mime: str) -> AssetKind:
    if mime.startswith("image/"):
        return AssetKind.image
    if mime.startswith("video/"):
        return AssetKind.video
    if mime.startswith("audio/"):
        return AssetKind.audio
    raise HTTPException(415, f"unsupported file type {mime!r}; upload an image, video or audio file")


@router.post("/uploads", response_model=AssetOut, status_code=201)
async def upload(
    file: UploadFile, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> AssetOut:
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    kind = _kind_for(mime)
    storage = get_storage()
    asset_id = uuid7()
    ext = mimetypes.guess_extension(mime) or ""
    if ext == ".jpe":
        ext = ".jpg"
    tmp = storage.temp_dir() / f"upload-{asset_id.hex}{ext}"
    digest = hashlib.sha256()
    size = 0
    try:
        async with await anyio.open_file(tmp, "wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "file too large")
                digest.update(chunk)
                await out.write(chunk)
        try:
            info = await probe(tmp)
        except FFmpegError:
            raise HTTPException(415, "could not read this file as media") from None
        key = asset_key(user.id, asset_id, f"original{ext}")
        storage.put_file(key, tmp, move=True)
    finally:
        if tmp.exists():
            tmp.unlink()

    asset = Asset(
        id=asset_id, user_id=user.id, kind=kind, status=AssetStatus.ready, source=AssetSource.upload, mime=mime,
        storage_key=key, size_bytes=size, content_hash=digest.hexdigest(), width=info.width, height=info.height,
        duration_ms=info.duration_ms if kind != AssetKind.image else None, fps=info.fps if kind == AssetKind.video else None,
        has_audio=info.has_audio, codec=info.codec, title=(file.filename or "upload")[:200],
        metadata_={"original_filename": file.filename},
    )
    session.add(asset)
    await session.flush()
    await fsm.emit(session, user_id=user.id, topic="asset", type="asset.created", subject_id=asset_id,
                   payload={"asset_id": str(asset_id), "kind": kind.value})
    await session.commit()
    await defer_derive(asset_id)
    return asset_out(asset)


def _cursor(a: Asset) -> str:
    return base64.urlsafe_b64encode(f"{a.created_at.isoformat()}|{a.id}".encode()).decode()


@router.get("/assets", response_model=AssetPage)
async def list_assets(
    kind: Literal["video", "image", "audio"] | None = None,
    source: Literal["upload", "generation"] | None = None,
    favorite: bool | None = None,
    limit: int = Query(40, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AssetPage:
    stmt = select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(Asset.kind == AssetKind(kind))
    if source:
        stmt = stmt.where(Asset.source == AssetSource(source))
    if favorite is not None:
        stmt = stmt.where(Asset.favorite == favorite)
    if cursor:
        try:
            ts, aid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
            ts_dt, aid_u = datetime.fromisoformat(ts), uuid.UUID(aid)
        except Exception:
            raise HTTPException(400, "bad cursor") from None
        stmt = stmt.where(or_(Asset.created_at < ts_dt, and_(Asset.created_at == ts_dt, Asset.id < aid_u)))
    rows = (await session.execute(stmt.order_by(Asset.created_at.desc(), Asset.id.desc()).limit(limit + 1))).scalars().all()
    page, more = rows[:limit], len(rows) > limit
    return AssetPage(items=[asset_out(a) for a in page], next_cursor=_cursor(page[-1]) if more else None)


async def _owned(session: AsyncSession, user: User, asset_id: uuid.UUID, lock: bool = False) -> Asset:
    asset = await session.get(Asset, asset_id, with_for_update=lock)
    if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
        raise HTTPException(404, "asset not found")
    return asset


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                    user: User = Depends(current_user)) -> AssetOut:
    return asset_out(await _owned(session, user, asset_id))


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def patch_asset(asset_id: uuid.UUID, body: AssetPatch, session: AsyncSession = Depends(get_session),
                      user: User = Depends(current_user)) -> AssetOut:
    asset = await _owned(session, user, asset_id, lock=True)
    if body.title is not None:
        asset.title = body.title[:200]
    if body.tags is not None:
        asset.tags = [t[:64] for t in body.tags][:50]
    if body.favorite is not None:
        asset.favorite = body.favorite
    await session.commit()
    return asset_out(asset)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> None:
    """Soft delete: hidden from the library; files stay until a purge job (later) removes them."""
    asset = await _owned(session, user, asset_id, lock=True)
    asset.deleted_at = datetime.now(UTC)
    await fsm.emit(session, user_id=user.id, topic="asset", type="asset.deleted", subject_id=asset_id,
                   payload={"asset_id": str(asset_id)})
    await session.commit()


@router.get("/assets/{asset_id}/lineage", response_model=AssetLineageOut)
async def lineage(asset_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                  user: User = Depends(current_user)) -> AssetLineageOut:
    await _owned(session, user, asset_id)
    parents = (await session.execute(select(AssetLineage).where(AssetLineage.child_asset_id == asset_id))).scalars()
    children = (await session.execute(select(AssetLineage).where(AssetLineage.parent_asset_id == asset_id))).scalars()

    def edge(e: AssetLineage) -> LineageEdge:
        return LineageEdge(parent_asset_id=e.parent_asset_id, child_asset_id=e.child_asset_id,
                           relation=e.relation, generation_id=e.generation_id)

    return AssetLineageOut(parents=[edge(e) for e in parents], children=[edge(e) for e in children])

