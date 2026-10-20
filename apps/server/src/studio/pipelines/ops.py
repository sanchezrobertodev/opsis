"""Local step operations, keyed by the step input's `op`."""

from __future__ import annotations

import mimetypes
import shutil
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.base import uuid7
from studio.db.models import Asset, Element, ElementAsset, PipelineRun, PipelineStep
from studio.domain.derived import frame_of, save_derived
from studio.domain.enums import AssetKind, AssetSource
from studio.faceless.catalog import CaptionLook, get_catalog
from studio.media.assemble import Block, assemble_episode, frame_size
from studio.media.captions import burn_captions, cues, fit_words, line_cues, place_words, to_ass, to_srt
from studio.media.ffmpeg import ffmpeg, probe
from studio.media.ops import concat_clips
from studio.media.speech import join_speech
from studio.media.swap import masked_merge
from studio.media.timeline import FPS, Card, Clip, Sound, cut_film, cut_timeline, normalize_clip
from studio.media.transcript import read as read_transcript
from studio.media.transcript import shots as lyric_cuts
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
                            _seconds(voice), hold=bool(b.get("hold"))))  # a clip's own sound: see clip_sound_db
    work = storage.temp_dir() / f"episode-{uuid7().hex}"
    dest = work / "master.mp4"
    try:
        tl = await assemble_episode(
            blocks, dest, work, size=frame_size(inp["aspect_ratio"], inp.get("resolution", "1080p")),
            music=storage.path_for(assets[music_id].storage_key) if music_id else None,
            **{k: float(inp[k]) for k in ("gap_s", "tail_s", "clip_sound_db") if inp.get(k) is not None})
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
    style = get_catalog().style(inp.get("style", ""))
    look = style.caption if style else CaptionLook()
    if inp.get("sung"):  # a song: its lines, each in its share of the section after the bar of lead-in
        lead = float(inp.get("lead_in") or 0)
        items = line_cues([(t + lead, t + _seconds(v), lines)
                           for v, t, lines in zip(voices, inp["starts"], inp["sung"], strict=True)],
                          max_chars=look.max_chars)
    elif inp.get("words"):  # word timings per block, already known (a song's transcript)
        words = inp["words"]
        items = cues(place_words(words, inp["starts"]), max_chars=look.max_chars)
    else:
        words = [(v.metadata_ or {}).get("alignment", {}).get("words") or fit_words(text, _seconds(v))
                 for v, text in zip(voices, inp["narrations"], strict=True)]
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


async def extract_audio(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """A clip's sound as an audio asset (an MP3), for steps that work on speech."""
    (clip,) = await _assets(session, run, [step.input["video"]])
    if not clip.has_audio:
        raise StepFailed("the clip has no sound")
    storage = get_storage()
    work = storage.temp_dir() / f"audio-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        dest = work / "audio.mp3"  # small enough to upload: a minute is about 1.4 MB
        await ffmpeg("-i", str(storage.path_for(clip.storage_key)), "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
                     str(dest))
        audio = await save_derived(session, run.user_id, dest, kind=AssetKind.audio, source=AssetSource.pipeline,
                                   title=f"Sound of {clip.title or 'a clip'}", parents=[(clip.id, "audio_of")],
                                   pipeline_run_id=run.id, metadata={"recipe": run.recipe})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(audio.id)}


async def replace_audio(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """The clip with new sound: its picture as it was, the given audio under it, as long as the picture."""
    inp = step.input
    clip, audio = await _assets(session, run, [inp["video"], inp["audio"]])
    storage = get_storage()
    work = storage.temp_dir() / f"revoice-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        dest = work / "revoiced.mp4"
        await ffmpeg("-i", str(storage.path_for(clip.storage_key)), "-i", str(storage.path_for(audio.storage_key)),
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                     "-af", "apad", "-shortest", str(dest))
        out = await save_derived(session, run.user_id, dest, kind=AssetKind.video, source=AssetSource.pipeline,
                                 title=inp.get("title") or clip.title or run.title,
                                 parents=[(clip.id, "picture_from"), (audio.id, "sound_from")],
                                 pipeline_run_id=run.id, metadata={"recipe": run.recipe})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(out.id), "derive": [str(out.id)]}


async def lyric_shots(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Cut a song into shots at the pauses in its lyrics, from its transcript. Each shot keeps its words, timed from
    its own start (for captions); `seconds` are the shots' lengths (for cutting the song to match)."""
    inp = step.input
    song, script = await _assets(session, run, [inp["audio"], inp["transcript"]])
    total = _seconds(song)
    if total <= 0:
        raise StepFailed("the song has no length")
    longest = float(inp["max_shots"]) * float(inp["max_s"])
    if total > longest:
        raise StepFailed(f"the song runs {total:.0f} s; at most {inp['max_shots']} shots of {inp['max_s']:.0f} s "
                         f"cover {longest:.0f} s. Raise the shot limit or trim the song")
    target = max(float(inp["target_s"]), total / float(inp["max_shots"]))
    words = read_transcript(get_storage().path_for(script.storage_key))
    cut = lyric_cuts(words, total, target_s=target, min_s=min(float(inp["min_s"]), target),
                     max_s=float(inp["max_s"]), max_shots=int(inp["max_shots"]))
    return {"shots": cut, "seconds": [round(s["end"] - s["start"], 3) for s in cut], "lyrics": bool(words)}


async def split_audio(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Cut one audio file into consecutive pieces of the given lengths (a song into its sections); the last piece
    runs to the end. Each piece is an audio asset."""
    inp = step.input
    (audio,) = await _assets(session, run, [inp["audio"]])
    lengths = [float(s) for s in inp["seconds"]]
    total = _seconds(audio)
    if not lengths or total <= 0:
        raise StepFailed("there's no audio to cut")
    storage = get_storage()
    work = storage.temp_dir() / f"split-{uuid7().hex}"
    work.mkdir(parents=True)
    pieces = []
    try:
        start = 0.0
        for i, length in enumerate(lengths):
            last = i == len(lengths) - 1
            dest = work / f"piece_{i:02d}.wav"
            span = ["-t", f"{length:.3f}"] if not last else []
            await ffmpeg("-ss", f"{start:.3f}", "-i", str(storage.path_for(audio.storage_key)), *span,
                         "-c:a", "pcm_s16le", str(dest))
            start += length
            piece = await save_derived(
                session, run.user_id, dest, kind=AssetKind.audio, source=AssetSource.pipeline,
                title=f"{inp.get('title') or audio.title}: part {i + 1}", parents=[(audio.id, "cut_from")],
                pipeline_run_id=run.id, metadata={"recipe": run.recipe, "part": i + 1})
            pieces.append(str(piece.id))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_ids": pieces, "asset_id": pieces[0]}


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


async def lora_dataset(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Zip a character's photos for a LoRA trainer (image_001.jpg, ...), kept as an archive asset."""
    inp = step.input
    if inp.get("element_id"):
        await _character(session, run, inp["element_id"])  # before the paid trainer, not after it
    photos = await _assets(session, run, inp["photos"])
    if any(a.kind != AssetKind.image for a in photos):
        raise StepFailed("only pictures can train a character")
    storage = get_storage()
    work = storage.temp_dir() / f"dataset-{uuid7().hex}"
    work.mkdir(parents=True)
    try:
        dest = work / "photos.zip"

        def pack() -> None:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
                for i, a in enumerate(photos, 1):
                    src = storage.path_for(a.storage_key)
                    z.write(src, f"image_{i:03d}{src.suffix.lower() or '.jpg'}")

        await anyio.to_thread.run_sync(pack)
        out = await save_derived(
            session, run.user_id, dest, kind=AssetKind.archive, source=AssetSource.pipeline,
            title=inp.get("title") or run.title, pipeline_run_id=run.id,
            parents=[(a.id, "photo") for a in photos], metadata={"recipe": run.recipe, "photos": len(photos)},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"asset_id": str(out.id)}


async def attach_lora(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Store a trained identity on the character: the LoRA files for each expert, the base and the trigger word."""
    inp = step.input
    files = await _assets(session, run, inp["assets"])
    by_expert = {str((a.metadata_ or {}).get("expert") or "low"): a for a in files if a.kind == AssetKind.lora}
    low = by_expert.get("low")
    if low is None:
        raise StepFailed("the trainer returned no LoRA file")
    element = await _character(session, run, inp["element_id"])
    high = by_expert.get("high")
    lora = {"base": (low.metadata_ or {}).get("base"), "trigger": inp["trigger"], "low": str(low.id),
            "high": str(high.id) if high else None, "steps": inp.get("steps"), "run_id": str(run.id),
            "trained_at": datetime.now(UTC).isoformat()}
    element.lora = lora
    return lora


async def add_element_image(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    """Keep a picture as one of a character's references (its first one becomes the cover and primary picture)."""
    inp = step.input
    element = await _character(session, run, inp["element_id"])
    (picture,) = await _assets(session, run, [inp["asset_id"]])
    if picture.kind != AssetKind.image:
        raise StepFailed("only a picture can be a character's reference")
    links = (await session.execute(select(ElementAsset).where(ElementAsset.element_id == element.id))).scalars().all()
    if not any(x.asset_id == picture.id for x in links):  # a rerun of the step keeps one link
        session.add(ElementAsset(element_id=element.id, asset_id=picture.id, view=inp.get("view") or "front",
                                 is_primary=not links, sort=len(links)))
    if element.cover_asset_id is None:
        element.cover_asset_id = picture.id
    return {"element_id": str(element.id), "asset_id": str(picture.id)}


async def _character(session: AsyncSession, run: PipelineRun, element_id: str) -> Element:
    """The run owner's character that a trained identity is for; it must still exist."""
    try:
        eid = uuid.UUID(str(element_id))
    except ValueError:
        raise StepFailed("that isn't a character id") from None
    element = (await session.execute(select(Element).where(
        Element.id == eid, Element.user_id == run.user_id, Element.deleted_at.is_(None)))).scalar_one_or_none()
    if element is None:
        raise StepFailed("the character is gone")
    return element


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
    "split_audio": split_audio,
    "lyric_shots": lyric_shots,
    "extract_audio": extract_audio,
    "replace_audio": replace_audio,
    "lora_dataset": lora_dataset,
    "attach_lora": attach_lora,
    "add_element_image": add_element_image,
}
