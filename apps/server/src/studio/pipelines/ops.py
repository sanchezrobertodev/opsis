"""Local step operations, keyed by the step input's `op`."""

from __future__ import annotations

import mimetypes
import shutil
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.base import uuid7
from studio.db.models import Asset, PipelineRun, PipelineStep
from studio.domain.derived import frame_of, save_derived
from studio.domain.enums import AssetKind, AssetSource
from studio.faceless.catalog import CaptionLook, get_catalog
from studio.media.assemble import Block, assemble_episode, frame_size
from studio.media.captions import burn_captions, cues, fit_words, place_words, to_ass, to_srt
from studio.media.ffmpeg import probe
from studio.media.ops import concat_clips
from studio.media.speech import join_speech
from studio.media.swap import masked_merge
from studio.media.timeline import FPS, Card, Clip, Sound, cut_film, cut_timeline, normalize_clip
from studio.storage.local import get_storage

mimetypes.add_type("application/x-subrip", ".srt")


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


async def first_frame(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    (video,) = await _assets(session, run, [step.input["asset_id"]])
    frame, _ = await frame_of(session, video, "first", pipeline_run_id=run.id)
    return {"asset_id": str(frame.id), "derive": [str(frame.id)]}


def take_metadata(inp: dict[str, Any], made_by: str, parent: uuid.UUID, **extra: Any) -> dict[str, Any]:
    """Marks a derived clip as a take of a Cinema shot (GET /projects/{id}/takes lists it) with its parent take."""
    return {"project_id": inp["project_id"], "shot_key": inp["shot_key"], "made_by": made_by,
            "parent_asset_id": str(parent), "prompt": inp.get("prompt", ""), **extra}


async def split_shots(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """A multi-shot take cut back into one take per shot, at the shot lengths it was asked for (scaled to the
    clip's real length when the model ran a little long or short)."""
    inp = step.input
    (take,) = await _assets(session, run, [inp["asset_id"]])
    storage = get_storage()
    src = storage.path_for(take.storage_key)
    info = await probe(src)
    asked = [float(s["duration"]) for s in inp["shots"]]
    scale = ((info.duration_ms or 0) / 1000 / sum(asked)) if info.duration_ms else 1.0
    size = ((info.width or 1280) // 2 * 2, (info.height or 720) // 2 * 2)
    work = storage.temp_dir() / f"split-{uuid7().hex}"
    work.mkdir(parents=True)
    out: dict[str, str] = {}
    try:
        start = 0.0
        for i, (shot, seconds) in enumerate(zip(inp["shots"], asked, strict=True), 1):
            end = start + seconds * scale
            dest = work / f"shot_{i:02d}.mp4"
            await normalize_clip(Clip(src, start, end), dest, size)
            part = await save_derived(
                session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
                title=f"{run.title}: shot {i}", pipeline_run_id=run.id, parents=[(take.id, "multi_shot_take")],
                metadata=take_metadata({**inp, "shot_key": shot["key"], "prompt": shot.get("prompt", "")},
                                       "multi_shot", take.id, scene_key=inp.get("scene_key"),
                                       start=round(start, 3), end=round(end, 3)),
            )
            out[shot["key"]] = str(part.id)
            start = end
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"assets": out, "derive": list(out.values())}


async def join_takes(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """A take extended forward or backward: the clips joined end to end into a new take of the same shot."""
    inp = step.input
    assets = await _assets(session, run, [c["asset_id"] for c in inp["clips"]])
    storage = get_storage()
    clips = [Clip(storage.path_for(a.storage_key), c.get("trim_in", 0.0), c.get("trim_out"))
             for a, c in zip(assets, inp["clips"], strict=True)]
    parent = uuid.UUID(inp["parent_asset_id"])
    work = storage.temp_dir() / f"join-{uuid7().hex}"
    try:
        dest = work / "take.mp4"
        total = await cut_timeline(clips, dest, work)
        take = await save_derived(
            session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline, title=run.title,
            pipeline_run_id=run.id,
            parents=[(a.id, "extended_from" if a.id == parent else "extension") for a in assets],
            metadata=take_metadata(inp, inp["made_by"], parent),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(take.id), "duration_ms": round(total * 1000), "derive": [str(take.id)]}


async def join_audio(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Spoken lines strung into one dialogue track, in order (a shot's lines, each in its speaker's voice)."""
    inp = step.input
    lines = await _assets(session, run, inp["asset_ids"])
    storage = get_storage()
    work = storage.temp_dir() / f"lines-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        dest = work / "dialogue.mp3"
        await join_speech([storage.path_for(a.storage_key) for a in lines], dest, gap=inp.get("gap", 0.35))
        seconds = (await probe(dest)).duration_ms
        track = await save_derived(
            session, run.user_id, dest, kind=AssetKind.audio, source=AssetSource.pipeline,
            title=inp.get("title") or f"{run.title}: dialogue", pipeline_run_id=run.id,
            parents=[(a.id, f"line_{i:02d}") for i, a in enumerate(lines, 1)],
            metadata={"recipe": run.recipe, "lines": len(lines), **({"shot_key": inp["shot_key"]}
                                                                    if inp.get("shot_key") else {})},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(track.id), "duration_ms": seconds, "derive": [str(track.id)]}


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


def _seconds(asset: Asset) -> float:
    return (asset.duration_ms or 0) / 1000


async def narrated_cut(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Assemble a narrated episode: one visual per narration block, plus optional music. No captions."""
    inp = step.input
    blocks_in: list[dict[str, str]] = inp["blocks"]
    music_id = inp.get("music")
    ids = [b["visual"] for b in blocks_in] + [b["voice"] for b in blocks_in] + ([music_id] if music_id else [])
    assets = {str(a.id): a for a in await _assets(session, run, ids)}
    storage = get_storage()
    blocks = []
    for b in blocks_in:
        visual, voice = assets[b["visual"]], assets[b["voice"]]
        if _seconds(voice) <= 0:
            raise StepFailed(f"the narration {voice.id} has no length")
        kind = "video" if visual.kind == AssetKind.video else "image"
        blocks.append(Block(storage.path_for(visual.storage_key), kind, storage.path_for(voice.storage_key),
                            _seconds(voice)))
    work = storage.temp_dir() / f"episode-{uuid7().hex}"
    dest = work / "master.mp4"
    try:
        tl = await assemble_episode(
            blocks, dest, work, size=frame_size(inp["aspect_ratio"], inp.get("resolution", "1080p")),
            music=storage.path_for(assets[music_id].storage_key) if music_id else None)
        parents = [(assets[b["visual"]].id, f"block_{i:02d}") for i, b in enumerate(blocks_in, 1)]
        parents += [(assets[b["voice"]].id, f"narration_{i:02d}") for i, b in enumerate(blocks_in, 1)]
        if music_id:
            parents.append((assets[music_id].id, "music"))
        master = await save_derived(
            session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
            title=inp.get("title") or run.title, parents=parents, pipeline_run_id=run.id,
            metadata={"recipe": run.recipe, "starts": tl.starts, "durations": tl.durations},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(master.id), "starts": tl.starts, "durations": tl.durations, "derive": [str(master.id)]}


async def captions(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Burn captions into a narrated cut from the narration's word timings; also keep them as an SRT."""
    inp = step.input
    (video,) = await _assets(session, run, [inp["video"]])
    voices = await _assets(session, run, inp["voices"])
    words = [(v.metadata_ or {}).get("alignment", {}).get("words") or fit_words(text, _seconds(v))
             for v, text in zip(voices, inp["narrations"], strict=True)]
    style = get_catalog().style(inp.get("style", ""))
    look = style.caption if style else CaptionLook()
    items = cues(place_words(words, inp["starts"]), max_chars=look.max_chars)
    if not items:
        raise StepFailed("the narration has no words to caption")
    storage = get_storage()
    work = storage.temp_dir() / f"captions-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        srt_path = work / "captions.srt"
        srt_path.write_text(to_srt(items), encoding="utf-8")
        dest = work / "captioned.mp4"
        await burn_captions(storage.path_for(video.storage_key),
                            to_ass(items, look, video.width or 1920, video.height or 1080), dest, work)
        title = inp.get("title") or video.title or run.title
        srt = await save_derived(session, run.user_id, srt_path, kind=AssetKind.subtitle, source=AssetSource.pipeline,
                                 title=f"Captions: {title}", parents=[(video.id, "captions_for")],
                                 pipeline_run_id=run.id, metadata={"cues": len(items)})
        out = await save_derived(session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
                                 title=title, parents=[(video.id, "captioned_from"), (srt.id, "captions")],
                                 pipeline_run_id=run.id, metadata={"recipe": run.recipe, "captions": str(srt.id)})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(out.id), "subtitle_asset_id": str(srt.id), "derive": [str(out.id)]}


async def keep_background(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Composite an edited take over the original through the object's matte (object swap)."""
    inp = step.input
    original, edited, matte = await _assets(session, run, [inp["original"], inp["edited"], inp["matte"]])
    storage = get_storage()
    work = storage.temp_dir() / f"merge-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        dest = work / "merged.mp4"
        await masked_merge(storage.path_for(original.storage_key), storage.path_for(edited.storage_key),
                           storage.path_for(matte.storage_key), dest, grow=inp.get("grow", 6),
                           feather=inp.get("feather", 4))
        keep = {k: v for k, v in (edited.metadata_ or {}).items() if k in ("model_id", "prompt")}
        out = await save_derived(
            session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
            title=inp.get("title") or edited.title or run.title, pipeline_run_id=run.id,
            parents=[(original.id, "original"), (edited.id, "edited"), (matte.id, "matte")],
            metadata={**keep, "recipe": run.recipe, "kept_background": True,
                      **(take_metadata(inp["cinema"], "region_edit", original.id) if inp.get("cinema") else {})},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(out.id), "derive": [str(out.id)]}


async def timeline_cut(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """The film: takes and title cards in order, each trimmed, with cuts or fades between them, and the dialogue,
    music and SFX tracks mixed under them. Older runs list plain `clips`."""
    inp = step.input
    entries = inp.get("items") or [{"take": c} for c in inp.get("clips", [])]
    takes = [e["take"] for e in entries if e.get("take")]
    sounds_in = inp.get("audio") or []
    assets = await _assets(session, run, [t["asset_id"] for t in takes])
    audio = await _assets(session, run, [a["asset_id"] for a in sounds_in])
    storage = get_storage()
    by_id = {str(a.id): a for a in [*assets, *audio]}
    items: list[Clip | Card] = []
    for e in entries:
        if t := e.get("take"):
            items.append(Clip(storage.path_for(by_id[t["asset_id"]].storage_key), t.get("trim_in", 0.0),
                              t.get("trim_out"), t.get("transition", "cut"), t.get("volume", 1.0)))
        else:
            c = e["card"]
            items.append(Card(c["text"], c.get("subtext", ""), c.get("duration", 3.0), c.get("background", "#0d0e10"),
                              c.get("color", "#f2efe8"), c.get("transition", "fade")))
    if items and items[0].transition == "fade":
        items[0].transition = "cut"  # nothing to fade from
    sounds = [Sound(storage.path_for(by_id[a["asset_id"]].storage_key), a["track"], a.get("start", 0.0),
                    a.get("trim_in", 0.0), a.get("length"), a.get("volume", 1.0), a.get("fade_in", 0.0),
                    a.get("fade_out", 0.0)) for a in sounds_in]
    work = storage.temp_dir() / f"cut-{uuid7().hex}"
    try:
        dest = work / "film.mp4"
        plan = await cut_film(items, dest, work, fade=inp.get("fade_seconds", 0.5), sounds=sounds,
                              duck_music=inp.get("duck_music", True))
        film = await save_derived(
            session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
            title=inp.get("title") or run.title, pipeline_run_id=run.id,
            parents=[*((a.id, f"shot_{i:02d}") for i, a in enumerate(assets, 1)),
                     *((a.id, f"{s['track']}_{i:02d}") for i, (a, s) in enumerate(zip(audio, sounds_in, strict=True), 1))],
            metadata={"recipe": run.recipe, "items": entries, "audio": sounds_in, "frames": plan.frames,
                      "fps": FPS, "starts": [seg.start for seg in plan.segments]},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(film.id), "duration_ms": round(plan.seconds * 1000), "frames": plan.frames,
            "derive": [str(film.id)]}


LOCAL_OPS: dict[str, Callable[[AsyncSession, PipelineRun, PipelineStep], Awaitable[dict[str, Any]]]] = {
    "timeline_cut": timeline_cut,
    "last_frame": last_frame,
    "first_frame": first_frame,
    "split_shots": split_shots,
    "join_takes": join_takes,
    "join_audio": join_audio,
    "concat": concat,
    "narrated_cut": narrated_cut,
    "captions": captions,
    "keep_background": keep_background,
}
