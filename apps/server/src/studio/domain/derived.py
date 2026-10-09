"""Save locally made media (frame grabs, assembled cuts) as library assets with lineage."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Literal

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.base import uuid7
from studio.db.models import Asset, AssetLineage
from studio.domain import fsm
from studio.domain.enums import AssetKind, AssetSource, AssetStatus
from studio.media.ffmpeg import Probe, probe
from studio.media.ops import extract_frame
from studio.storage.base import asset_key
from studio.storage.local import get_storage

mimetypes.add_type("application/zip", ".zip")  # Windows' registry says application/x-zip-compressed

# files that aren't pictures, clips or sound: stored as they are, never probed
UNPROBED = {AssetKind.model3d, AssetKind.splat, AssetKind.lora, AssetKind.archive, AssetKind.document}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


async def save_derived(
    session: AsyncSession,
    user_id: uuid.UUID,
    src: Path,
    *,
    kind: AssetKind,
    title: str,
    parents: list[tuple[uuid.UUID, str]],
    source: AssetSource = AssetSource.derived,
    metadata: dict | None = None,
    pipeline_run_id: uuid.UUID | None = None,
) -> Asset:
    """Move `src` into storage and record it. Caller commits, then defers derivatives (thumbnails)."""
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    info = await probe(src) if kind not in UNPROBED else Probe(None, None, None, None, False, None, None, {})
    digest = await anyio.to_thread.run_sync(_sha256, src)
    asset_id = uuid7()
    key = asset_key(user_id, asset_id, f"original{src.suffix}")
    size = get_storage().put_file(key, src, move=True)
    asset = Asset(
        id=asset_id, user_id=user_id, kind=kind, status=AssetStatus.ready, source=source, mime=mime,
        storage_key=key, size_bytes=size, content_hash=digest, width=info.width, height=info.height,
        duration_ms=info.duration_ms if kind in (AssetKind.video, AssetKind.audio) else None,
        fps=info.fps if kind == AssetKind.video else None, has_audio=info.has_audio, codec=info.codec,
        title=title[:200],
        metadata_={**(metadata or {}), **({"pipeline_run_id": str(pipeline_run_id)} if pipeline_run_id else {})},
    )
    session.add(asset)
    await session.flush()
    for parent_id, relation in parents:
        session.add(AssetLineage(child_asset_id=asset_id, parent_asset_id=parent_id, relation=relation,
                                 pipeline_run_id=pipeline_run_id))
    await fsm.emit(session, user_id=user_id, topic="asset", type="asset.created", subject_id=asset_id,
                   payload={"asset_id": str(asset_id), "kind": kind.value})
    return asset


async def frame_of(
    session: AsyncSession, video: Asset, at: Literal["first", "last"] | float = "last",
    *, pipeline_run_id: uuid.UUID | None = None,
) -> tuple[Asset, bool]:
    """The image asset for one frame of `video`, reusing an earlier grab. Returns (asset, created).

    Raises FFmpegError if the clip can't be read. Caller commits, then defers derivatives when created.
    """
    relation = f"{at}_frame" if isinstance(at, str) else f"frame@{at:.2f}"
    existing = (await session.execute(
        select(Asset).join(AssetLineage, AssetLineage.child_asset_id == Asset.id).where(
            AssetLineage.parent_asset_id == video.id, AssetLineage.relation == relation, Asset.deleted_at.is_(None))
    )).scalars().first()
    if existing:
        return existing, False
    tmp = get_storage().temp_dir() / f"frame-{uuid7().hex}.png"
    try:
        await extract_frame(get_storage().path_for(video.storage_key), tmp, at)
        label = {"first": "First frame", "last": "Last frame"}.get(str(at), f"Frame at {at}s")
        keep = {k: v for k, v in (video.metadata_ or {}).items() if k in ("prompt", "model_id")}
        frame = await save_derived(session, video.user_id, tmp, kind=AssetKind.image, parents=[(video.id, relation)],
                                   title=f"{label} of {video.title or 'clip'}",
                                   metadata={**keep, "frame_of": str(video.id), "at": at},
                                   pipeline_run_id=pipeline_run_id)
    finally:
        if tmp.exists():
            tmp.unlink()
    return frame, True
