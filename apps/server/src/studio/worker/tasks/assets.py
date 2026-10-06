"""Asset derivatives: poster, thumbnail, filmstrip sprite, and a proxy when the codec isn't browser-safe."""

from __future__ import annotations

import logging
import uuid

import anyio
from procrastinate.exceptions import AlreadyEnqueued

from studio.db.models import Asset
from studio.db.session import session_scope
from studio.domain import fsm
from studio.domain.enums import AssetKind
from studio.media import derive
from studio.media.ffmpeg import probe
from studio.storage.base import asset_key
from studio.storage.local import get_storage
from studio.worker.app import Q_CPU, app

log = logging.getLogger(__name__)


async def defer_derive(asset_id: uuid.UUID) -> None:
    try:
        await asset_derive.configure(queueing_lock=f"derive:{asset_id}").defer_async(asset_id=str(asset_id))
    except AlreadyEnqueued:
        pass


@app.task(name="asset.derive", queue=Q_CPU, retry=2)
async def asset_derive(asset_id: str) -> None:
    await run_derive(uuid.UUID(asset_id))


async def run_derive(asset_id: uuid.UUID) -> None:
    storage = get_storage()
    async with session_scope() as s:
        asset = await s.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            return
        user_id, kind, key, mime = asset.user_id, asset.kind, asset.storage_key, asset.mime
        duration_ms = asset.duration_ms
    src = storage.path_for(key)

    def k(name: str) -> str:
        return asset_key(user_id, asset_id, name)

    derivatives: dict[str, object] = {}
    if kind == AssetKind.video:
        poster = storage.path_for(k("poster.jpg"))
        await derive.video_poster(src, poster, duration_ms)
        derivatives["poster"] = k("poster.jpg")
        await anyio.to_thread.run_sync(derive.image_thumb, poster, storage.path_for(k("thumb.webp")))
        derivatives["thumb"] = k("thumb.webp")
        info = await probe(src)
        frames = await derive.video_filmstrip(src, storage.path_for(k("filmstrip.jpg")), info)
        derivatives["filmstrip"] = k("filmstrip.jpg")
        derivatives["filmstrip_frames"] = frames
        if derive.needs_proxy(info, mime):
            await derive.video_proxy(src, storage.path_for(k("proxy.mp4")))
            derivatives["proxy"] = k("proxy.mp4")
    elif kind in (AssetKind.image, AssetKind.panorama, AssetKind.mask, AssetKind.depth):
        await anyio.to_thread.run_sync(derive.image_thumb, src, storage.path_for(k("thumb.webp")))
        derivatives["thumb"] = k("thumb.webp")

    async with session_scope() as s:
        asset = await s.get(Asset, asset_id, with_for_update=True)
        if asset is None:
            return
        asset.derivatives = {**(asset.derivatives or {}), **derivatives}
        await fsm.emit(s, user_id=user_id, topic="asset", type="asset.updated", subject_id=asset_id,
                       payload={"asset_id": str(asset_id), "derivatives": list(derivatives)})
