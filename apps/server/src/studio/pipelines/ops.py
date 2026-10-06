"""Local step operations, keyed by the step input's `op`."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.base import uuid7
from studio.db.models import Asset, PipelineRun, PipelineStep
from studio.domain.derived import frame_of, save_derived
from studio.domain.enums import AssetKind, AssetSource
from studio.media.ops import concat_clips
from studio.storage.local import get_storage


class StepFailed(Exception):
    """A step failed in a way worth showing the user as-is."""


async def _assets(session: AsyncSession, run: PipelineRun, ids: list[str]) -> list[Asset]:
    wanted = [uuid.UUID(i) for i in ids]
    rows = {a.id: a for a in (await session.execute(
        select(Asset).where(Asset.id.in_(wanted), Asset.user_id == run.user_id, Asset.deleted_at.is_(None))
    )).scalars()}
    missing = [str(i) for i in wanted if i not in rows]
    if missing:
        raise StepFailed(f"input media is gone: {', '.join(missing)}")
    return [rows[i] for i in wanted]


async def last_frame(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    (video,) = await _assets(session, run, [step.input["asset_id"]])
    frame, _ = await frame_of(session, video, "last", pipeline_run_id=run.id)
    return {"asset_id": str(frame.id), "derive": [str(frame.id)]}


async def concat(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    clips = await _assets(session, run, step.input["asset_ids"])
    storage = get_storage()
    tmp = storage.temp_dir() / f"cut-{uuid7().hex}.mp4"
    try:
        await concat_clips([storage.path_for(c.storage_key) for c in clips], tmp)
        cut = await save_derived(
            session, run.user_id, tmp, kind=AssetKind.video, source=AssetSource.pipeline,
            title=step.input.get("title") or run.title, parents=[(c.id, f"clip_{i:02d}") for i, c in enumerate(clips, 1)],
            metadata={"recipe": run.recipe, "clips": [str(c.id) for c in clips]}, pipeline_run_id=run.id,
        )
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"asset_id": str(cut.id), "derive": [str(cut.id)]}


LOCAL_OPS: dict[str, Callable[[AsyncSession, PipelineRun, PipelineStep], Awaitable[dict[str, Any]]]] = {
    "last_frame": last_frame,
    "concat": concat,
}
