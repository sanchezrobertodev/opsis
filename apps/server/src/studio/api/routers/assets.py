from __future__ import annotations

import base64
import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import AssetLineageOut, AssetOut, AssetPage, AssetPatch, FrameIn, LineageEdge
from studio.api.serializers import asset_out
from studio.db.base import uuid7
from studio.db.models import Asset, AssetLineage, User
from studio.domain import fsm
from studio.domain.derived import frame_of
from studio.domain.enums import AssetKind, AssetSource, AssetStatus
from studio.media.derive import image_thumb
from studio.media.ffmpeg import FFmpegError, Probe, probe
from studio.storage.base import asset_key
from studio.storage.local import get_storage
from studio.worker.tasks.assets import defer_derive

router = APIRouter(tags=["assets"])

MAX_UPLOAD_BYTES = 4 * 1024**3
# formats that are lists of other files: probing or converting one reads whatever it names on this machine
POINTS_ELSEWHERE = {"hls", "dash", "concat", "applehttp"}
CHUNK = 1 << 20


# files the studio keeps without reading them as media: the first bytes each must start with
NOT_MEDIA = {AssetKind.model3d: (b"glTF",), AssetKind.splat: (b"ply\n", b"ply\r", b"\x1f\x8b")}


# types that say nothing about the file (scripts, curl and some tools send these): the file name decides instead
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream", "application/binary"}
# extensions Windows' type table can lack, or Python's own (used where the system has none, like the server's image)
EXTRA_TYPES = {".webp": "image/webp", ".m4a": "audio/mp4", ".mkv": "video/x-matroska", ".m4v": "video/x-m4v"}


def _type_of_name(filename: str) -> str | None:
    return mimetypes.guess_type(filename)[0] or EXTRA_TYPES.get(Path(filename).suffix.lower())


def _sent_or_guessed(content_type: str | None, filename: str) -> str:
    """The type the client sent, unless it's a generic one; then the one the file name implies."""
    sent = (content_type or "").split(";")[0].strip().lower()
    guessed = _type_of_name(filename)
    return (guessed if sent in GENERIC_TYPES else sent) or guessed or "application/octet-stream"


def _extension(mime: str, filename: str) -> str:
    """The file name's own extension when it agrees with the type (a .mkv stays .mkv, a .jpeg .jpeg), else the
    type's usual one."""
    suffix = Path(filename).suffix.lower()
    if suffix and _type_of_name(filename) == mime:
        return suffix
    ext = mimetypes.guess_extension(mime) or suffix
    ext = ".jpg" if ext == ".jpe" else ext
    # /media picks the served type from the name: one that isn't media (".html") would be served as a web page
    named = _type_of_name(f"x{ext}") or ""
    return ext if named.split("/")[0] in ("image", "video", "audio") or named == "model/gltf-binary" else ".bin"


def _names_other_files(start: bytes) -> bool:
    """A playlist or file list (HLS, DASH, ffconcat): ffprobe would open the files it names, so it must be refused
    before probing. These are text formats that announce themselves in their first lines."""
    text = start.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return text.startswith((b"#extm3u", b"ffconcat")) or (text.startswith(b"<") and b"<mpd" in text)


def _kind_for(mime: str, filename: str = "") -> AssetKind:
    name = filename.lower()
    if mime == "model/gltf-binary" or name.endswith(".glb"):
        return AssetKind.model3d
    if name.endswith((".spz", ".ply")):
        return AssetKind.splat
    if mime == "image/svg+xml" or name.endswith((".svg", ".svgz")):
        raise HTTPException(415, "SVG can't be uploaded: it can carry scripts; export it as PNG or WebP")
    if mime.startswith("image/"):
        return AssetKind.image
    if mime.startswith("video/"):
        return AssetKind.video
    if mime.startswith("audio/"):
        return AssetKind.audio
    raise HTTPException(415, f"unsupported file type {mime!r}; upload an image, video, audio or GLB file")


@router.post("/uploads", response_model=AssetOut, status_code=201)
async def upload(
    file: UploadFile, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> AssetOut:
    mime = _sent_or_guessed(file.content_type, file.filename or "")
    kind = _kind_for(mime, file.filename or "")
    if kind in NOT_MEDIA:
        mime = mimetypes.guess_type(file.filename or "")[0] or ("model/gltf-binary" if kind == AssetKind.model3d
                                                                else "application/octet-stream")
    storage = get_storage()
    asset_id = uuid7()
    ext = _extension(mime, file.filename or "")
    if kind == AssetKind.splat:
        ext = Path(file.filename or "").suffix.lower() or ".spz"
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
        async with await anyio.open_file(tmp, "rb") as fh:
            start = await fh.read(4096)
        if _names_other_files(start):
            raise HTTPException(415, "playlists and lists of other files can't be uploaded; upload the media itself")
        if kind in NOT_MEDIA:
            head = start[:4]
            if not head.startswith(NOT_MEDIA[kind]):
                raise HTTPException(415, f"this doesn't look like a {'GLB' if kind == AssetKind.model3d else 'splat'} file")
            info = Probe(None, None, None, None, False, None, None, {})
        else:
            try:
                info = await probe(tmp)
            except FFmpegError:
                raise HTTPException(415, "could not read this file as media") from None
            if set((info.format_name or "").split(",")) & POINTS_ELSEWHERE:
                raise HTTPException(415, "playlists and lists of other files can't be uploaded; upload the media itself")
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


@router.post("/assets/{asset_id}/thumbnail", response_model=AssetOut)
async def set_thumbnail(asset_id: uuid.UUID, file: UploadFile, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> AssetOut:
    """A picture for an asset that can't be thumbnailed here (a 3D model the browser drew)."""
    asset = await session.get(Asset, asset_id, with_for_update=True)
    if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
        raise HTTPException(404, "asset not found")
    if (file.content_type or "") not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(415, "the thumbnail must be PNG, JPEG or WebP")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "thumbnail too large")
    storage = get_storage()
    tmp = storage.temp_dir() / f"thumb-{uuid7().hex}"
    tmp.write_bytes(data)
    # media is cached as immutable per key, so a redrawn picture gets a new name instead of replacing the old one
    old = (asset.derivatives or {}).get("thumb")
    key = asset_key(user.id, asset.id, f"thumb-{uuid7().hex[-12:]}.webp")
    try:
        await anyio.to_thread.run_sync(image_thumb, tmp, storage.path_for(key))
    finally:
        tmp.unlink(missing_ok=True)
    asset.derivatives = {**(asset.derivatives or {}), "thumb": key}
    await fsm.emit(session, user_id=user.id, topic="asset", type="asset.updated", subject_id=asset.id,
                   payload={"asset_id": str(asset.id), "derivatives": ["thumb"]})
    await session.commit()
    if isinstance(old, str) and old != key:
        storage.delete_prefix(old)
    return asset_out(asset)


def _cursor(a: Asset) -> str:
    return base64.urlsafe_b64encode(f"{a.created_at.isoformat()}|{a.id}".encode()).decode()


@router.get("/assets", response_model=AssetPage)
async def list_assets(
    kind: Literal["video", "image", "audio", "model3d", "splat", "lora", "archive"] | None = None,
    source: Literal["upload", "generation"] | None = None,
    favorite: bool | None = None,
    tag: str | None = Query(None, description="Only assets with this tag (e.g. `driving` for movement clips)"),
    projection: Literal["equirectangular"] | None = Query(None, description="Only 360 panoramas"),
    limit: int = Query(40, ge=1, le=200),
    cursor: str | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> AssetPage:
    stmt = select(Asset).where(Asset.user_id == user.id, Asset.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(Asset.kind == AssetKind(kind))
    else:  # trained LoRAs and training zips are the studio's working files, listed only when asked for
        stmt = stmt.where(Asset.kind.not_in([AssetKind.lora, AssetKind.archive]))
    if source:
        stmt = stmt.where(Asset.source == AssetSource(source))
    if favorite is not None:
        stmt = stmt.where(Asset.favorite == favorite)
    if tag:
        stmt = stmt.where(Asset.tags.contains([tag]))
    if projection:
        stmt = stmt.where(Asset.metadata_["projection"].astext == projection)
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
    if body.add_tags:
        asset.tags = list(dict.fromkeys([*(asset.tags or []), *(t[:64] for t in body.add_tags)]))[:50]
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



@router.post("/assets/{asset_id}/frames", response_model=AssetOut)
async def grab_frame(asset_id: uuid.UUID, body: FrameIn, session: AsyncSession = Depends(get_session),
                     user: User = Depends(current_user)) -> AssetOut:
    """Save one frame of a video as an image, e.g. the last frame to continue a shot from. Idempotent."""
    video = await _owned(session, user, asset_id)
    if video.kind != AssetKind.video:
        raise HTTPException(422, "frames come from videos; this asset is not a video")
    try:
        frame, created = await frame_of(session, video, body.at)
    except FFmpegError as exc:
        raise HTTPException(422, f"could not read a frame from this clip: {str(exc)[-200:]}") from exc
    await session.commit()
    if created:
        await defer_derive(frame.id)
    return asset_out(frame)
