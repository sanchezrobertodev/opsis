"""Render passes of a 3D scene: the browser draws each frame (beauty, depth, normals) and uploads it here; finishing
turns each pass into a video (or a still when it's one frame) kept as an asset tied to the scene version it shows.

  POST /projects/{id}/renders                 -> a render id, for one scene version and frame size
  POST /projects/{id}/renders/{rid}/frames    -> a batch of frames of one pass (JPEG or PNG), numbered from `start`
  POST /projects/{id}/renders/{rid}/finish    -> one asset per pass; the uploaded frames are deleted
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import AssetOut
from studio.api.serializers import asset_out
from studio.db.models import Asset, Project, User
from studio.domain.derived import save_derived
from studio.domain.enums import AssetKind, AssetSource
from studio.media.ffmpeg import ffmpeg
from studio.storage.local import get_storage
from studio.worker.tasks.assets import defer_derive

router = APIRouter(prefix="/projects", tags=["renders"])

Pass = Literal["beauty", "depth", "normal"]
MAX_FRAME_BYTES = 25 * 1024 * 1024
FRAME_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}


class RenderStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, description="The scene version the frames show")
    passes: list[Pass] = Field(min_length=1, max_length=3)
    fps: int = Field(24, ge=1, le=60)
    frames: int = Field(ge=1, le=3600)
    width: int = Field(ge=64, le=3840)
    height: int = Field(ge=64, le=3840)
    times: list[float] | None = Field(None, description="Stills: the scene time of each frame, in seconds")

    @model_validator(mode="after")
    def _sane(self) -> RenderStart:
        if len(set(self.passes)) != len(self.passes):
            raise ValueError("each pass once")
        if self.times is not None and len(self.times) != self.frames:
            raise ValueError("one time per frame")
        return self


class RenderStarted(BaseModel):
    render_id: uuid.UUID


class RenderDone(BaseModel):
    assets: dict[str, AssetOut] = Field(default_factory=dict, description="A video per pass (or the still of a one-frame render)")
    stills: dict[str, list[AssetOut]] = Field(default_factory=dict, description="Stills renders: an image per time, per pass")


def _root() -> Path:
    return get_storage().temp_dir() / "renders"


async def _scene(session: AsyncSession, user: User, project_id: uuid.UUID) -> Project:
    p = await session.get(Project, project_id)
    if p is None or p.user_id != user.id or p.deleted_at is not None or p.kind != "jutsu3d":
        raise HTTPException(404, "scene not found")
    return p


def _meta(user: User, project_id: uuid.UUID, render_id: uuid.UUID) -> tuple[Path, dict]:
    folder = _root() / render_id.hex
    try:
        meta = json.loads((folder / "render.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "render not found") from None
    if meta["user_id"] != str(user.id) or meta["project_id"] != str(project_id):
        raise HTTPException(404, "render not found")
    return folder, meta


@router.post("/{project_id}/renders", response_model=RenderStarted, status_code=201)
async def start_render(project_id: uuid.UUID, body: RenderStart, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> RenderStarted:
    p = await _scene(session, user, project_id)
    if body.version > p.version:
        raise HTTPException(422, f"the scene has no version {body.version} yet")
    render_id = uuid.uuid4()
    folder = _root() / render_id.hex
    folder.mkdir(parents=True)
    meta = {**body.model_dump(), "user_id": str(user.id), "project_id": str(project_id),
            "created_at": datetime.now(UTC).isoformat()}
    (folder / "render.json").write_text(json.dumps(meta), encoding="utf-8")
    return RenderStarted(render_id=render_id)


@router.post("/{project_id}/renders/{render_id}/frames", status_code=204)
async def upload_frames(project_id: uuid.UUID, render_id: uuid.UUID, files: list[UploadFile],
                        render_pass: Pass = Query(alias="pass"), start: int = Query(0, ge=0),
                        user: User = Depends(current_user)) -> None:
    folder, meta = _meta(user, project_id, render_id)
    if render_pass not in meta["passes"]:
        raise HTTPException(422, f"this render has no {render_pass} pass")
    if start + len(files) > meta["frames"]:
        raise HTTPException(422, f"this render has {meta['frames']} frames")
    out = folder / render_pass
    out.mkdir(exist_ok=True)
    for i, f in enumerate(files):
        ext = FRAME_TYPES.get(f.content_type or "")
        if ext is None:
            raise HTTPException(415, "frames must be JPEG or PNG")
        data = await f.read(MAX_FRAME_BYTES + 1)
        if len(data) > MAX_FRAME_BYTES:
            raise HTTPException(413, "frame too large")
        for old in out.glob(f"{start + i:05d}.*"):
            old.unlink()
        async with await anyio.open_file(out / f"{start + i:05d}{ext}", "wb") as fh:
            await fh.write(data)


@router.post("/{project_id}/renders/{render_id}/finish", response_model=RenderDone)
async def finish_render(project_id: uuid.UUID, render_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> RenderDone:
    p = await _scene(session, user, project_id)
    folder, meta = _meta(user, project_id, render_id)
    frames: dict[str, list[Path]] = {}
    for render_pass in meta["passes"]:
        got = sorted((folder / render_pass).glob("*.*")) if (folder / render_pass).exists() else []
        if len(got) != meta["frames"]:
            raise HTTPException(422, f"the {render_pass} pass has {len(got)} of {meta['frames']} frames")
        frames[render_pass] = got

    # the scene's models are what the passes show, so they're the passes' parents
    model_ids = {o.get("asset_id") for o in p.document.get("objects", []) if o.get("asset_id")}
    models = (await session.execute(select(Asset).where(
        Asset.id.in_([uuid.UUID(a) for a in model_ids]), Asset.user_id == user.id))).scalars().all() if model_ids else []
    made: dict[str, Asset] = {}
    stills: dict[str, list[Asset]] = {}
    common = {"project_id": str(p.id), "scene_version": meta["version"], "render_id": str(render_id)}
    parents = [(m.id, "scene_model") for m in models]
    try:
        for render_pass, paths in frames.items():
            if meta.get("times"):  # stills at chosen times: one image each
                stills[render_pass] = [await save_derived(
                    session, user.id, path, kind=AssetKind.image, source=AssetSource.pipeline,
                    title=f"{p.title}: {render_pass} at {t:g} s", parents=parents,
                    metadata={**common, "pass": render_pass, "time": t}) for path, t in zip(paths, meta["times"], strict=True)]
                continue
            still = meta["frames"] == 1
            if still:
                dest = paths[0]
            else:
                dest = folder / f"{render_pass}.mp4"
                w, h = meta["width"] // 2 * 2, meta["height"] // 2 * 2
                await ffmpeg("-framerate", str(meta["fps"]), "-i", str(folder / render_pass / f"%05d{paths[0].suffix}"),
                             "-vf", f"scale={w}:{h}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest))
            made[render_pass] = await save_derived(
                session, user.id, dest, kind=AssetKind.image if still else AssetKind.video,
                source=AssetSource.pipeline, title=f"{p.title}: {render_pass}{' still' if still else ' pass'}",
                parents=[(m.id, "scene_model") for m in models],
                metadata={"project_id": str(p.id), "scene_version": meta["version"], "pass": render_pass,
                          "fps": meta["fps"], "frames": meta["frames"], "render_id": str(render_id),
                          **({"times": meta["times"]} if meta.get("times") else {})},
            )
        await session.commit()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    for a in [*made.values(), *(x for xs in stills.values() for x in xs)]:
        await defer_derive(a.id)
    return RenderDone(assets={k: asset_out(a) for k, a in made.items()},
                      stills={k: [asset_out(a) for a in xs] for k, xs in stills.items()})
