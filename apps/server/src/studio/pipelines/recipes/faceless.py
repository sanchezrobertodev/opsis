"""Faceless video: a topic (or your script) becomes a narrated, captioned episode with music.

Steps, keyed so edits only redo what they touch:
  script (Claude) -> review (approve the script) -> plan (Claude: blocks of narration + visuals)
  -> per block: voice[b01] (TTS), still[b01] (image), clip[b01] (animate the still; motion format only)
  -> music (optional) -> cut (assemble) -> captions (optional) ; thumbs -> thumb pick (optional)

Three formats: `stills` holds one image per spoken beat with a slow Ken Burns move; `motion` animates each
block's still into a clip as long as its narration. A clip that fails (moderation, say) falls back to its
still, so one bad block never sinks the episode. `song` (the Kids song mode) has Claude write lyrics in sections
instead, each with a picture; a music model sings them section by section (`song` op, fixed section lengths), the
song is cut at the sections and each piece plays under its picture, with the lyrics as captions:
  script (Claude writes the song) -> review -> still[bNN] per section ; sing -> parts -> cut -> captions ; thumbs
Models are chosen by what they do (tts, t2i, start frame, music, song), so any registry model with those modes works.

A saved `character` (an @handle) can star: Claude marks the shots it appears in, and those stills (and the thumbnail)
are drawn from its reference pictures, so it looks the same throughout. With `clip_sound` (motion), each clip is made
with its own sound and the cut keeps it under the narration, ducked with the music (wildlife, weather, footsteps).
"""

from __future__ import annotations

import math
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import (
    LINES_PER_SECTION,
    MAX_SECTIONS,
    MAX_SONG_S,
    SECTION_PAD_S,
    WORDS_PER_MINUTE,
    Song,
    block_seconds,
    fit_song,
    section_seconds,
    song_from_text,
    song_lines,
)
from studio.media.assemble import CLIP_SOUND_DB, GAP_S
from studio.media.speech import CHARS_PER_SECOND
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    clip_params,
    mode_with_op,
    music_params,
    start_frame_mode,
    text_mode,
    with_param,
)
from studio.pipelines.recipes.vlog import SLUG, frame_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec


class FacelessInput(BaseModel):
    channel_id: uuid.UUID | None = None
    topic: str = Field("", max_length=300)
    script: str | None = Field(None, max_length=20000, description="Your own narration; skips writing one")
    title: str | None = Field(None, max_length=120)
    theme: str = "storytelling"
    style: str = "watercolor-chronicle"
    format: Literal["stills", "motion", "song"] = "stills"
    minutes: float = Field(1.0, ge=0.5, le=5, description="Target length when Claude writes the script")
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    resolution: Literal["720p", "1080p"] = "1080p"
    language: str = Field("English", max_length=40)
    notes: str = Field("", max_length=1000, description="Guidance for the writer")
    voice_model: str = "eleven-v4"
    voice: dict[str, Any] = Field(default_factory=dict, description="Voice model params, e.g. voice_id and tier")
    image_model: str = "nano-banana-2"
    image_params: dict[str, Any] = {}
    video_model: str = "kling-3.0"
    video_params: dict[str, Any] = {}
    music: bool = True
    music_model: str = "eleven-music"
    captions: bool = True
    thumbnail: bool = True
    review_script: bool = Field(True, description="Stop for approval once the script is written")
    character: str | None = Field(None, pattern=SLUG, description="A saved character's @handle who stars: its "
                                  "pictures keep its look in every shot it appears in")
    clip_sound: bool = Field(False, description="Motion: make each clip with its own sound and keep it under the "
                             "narration (ambience, animals, footsteps), ducked with the music")


def voice_key(i: int) -> str:
    return f"voice[b{i:02d}]"


def still_key(i: int) -> str:
    return f"still[b{i:02d}]"


def clip_key(i: int) -> str:
    return f"clip[b{i:02d}]"


def visual_prompt(visual: str, roster: list[dict[str, Any]], world: str, style: str) -> str:
    """One block's image prompt: the shot, the look of anyone recurring in it, the world, then the style."""
    looks = [f"{r['name']}: {r['look']}" for r in roster if r.get("name") and r["name"].lower() in visual.lower()]
    parts = [visual.strip().rstrip("."), *looks, world.strip().rstrip("."), style.strip().rstrip("."),
             "no text, letters or logos in the image"]
    return ". ".join(p for p in parts if p) + "."


def thumbnail_prompt(title: str, world: str, style: str, star: str | None = None) -> str:
    subject = f" The subject is @{star}." if star else ""
    return (f'Eye-catching video thumbnail for "{title}". One bold focal subject, simple uncluttered composition, '
            f"strong contrast, the title in large clean lettering.{subject} {world.strip().rstrip('.')}. "
            f"{style.strip()}")


def starring(visual: str, star: str) -> str:
    """A shot the star is in: it's named by its @handle, so the mention attaches its pictures."""
    return f"{visual.strip().rstrip('.')}. The star is @{star}"


# the scene's own sound, the creature's calls and movement included; only a score and speech are kept out
CLIP_SOUND_PROMPT = "Natural sound of the scene only (the animals' calls and movement, wind, water): no music, no speech"


def named(visual: str, star_name: str | None) -> str:
    """A clip's shot with the star called by name: a video model sees the picture, but "the star" means nothing."""
    return re.sub(r"\bthe star\b", star_name, visual, flags=re.IGNORECASE) if star_name else visual


class FacelessRecipe(Recipe):
    id = "faceless"
    name = "Faceless video"
    summary = "A narrated, captioned video from a topic or your script."
    Input = FacelessInput

    def title(self, inp: FacelessInput) -> str:
        return f"Faceless: {(inp.title or inp.topic or (inp.script or '')[:60]).strip()[:100]}"

    # ---------------------------------------------------------------- checks and estimate

    async def resolve(self, session: Any, user_id: uuid.UUID, inp: FacelessInput, reg: Registry) -> FacelessInput:
        """A starring character must exist and have a picture: checked when a run starts or is edited, so a deleted
        one is named as the problem instead of surfacing as a still's missing reference picture."""
        if inp.character and inp.format != "song":
            from studio.domain.generations import GenerationRequestError
            from studio.pipelines.llm_ops import (
                find_star,  # local: llm_ops imports the writer and the catalog
            )

            problem = (await find_star(session, user_id, inp.character))[1]
            if problem is not None:
                raise GenerationRequestError([problem])
        return inp

    def validate(self, inp: FacelessInput, reg: Registry) -> list[str]:
        cat = get_catalog()
        errors = []
        if cat.theme(inp.theme) is None:
            errors.append(f"unknown theme {inp.theme!r}")
        if cat.style(inp.style) is None:
            errors.append(f"unknown style {inp.style!r}")
        if not inp.topic.strip() and not (inp.script or "").strip():
            errors.append("give a topic or paste a script")
        needs = [(inp.image_model, None, "make images from text")]
        if inp.format == "song":
            needs.append((inp.music_model, "song", "sing a song from its lyrics"))
        else:
            needs.append((inp.voice_model, "tts", "speak the narration"))
            if inp.music:
                needs.append((inp.music_model, "music", "make music"))
        for model_id, op, what in needs:
            spec = reg.model(model_id)
            if spec is None:
                errors.append(f"unknown model {model_id!r}")
            elif (mode_with_op(spec, op) if op else (text_mode(spec) if spec.output_type == "image" else None)) is None:
                errors.append(f"{spec.name} can't {what}")
        if inp.format == "motion":
            spec = reg.model(inp.video_model)
            if spec is None:
                errors.append(f"unknown model {inp.video_model!r}")
            elif spec.output_type != "video" or start_frame_mode(spec) is None:
                errors.append(f"{spec.name} can't animate a still")
        # a star and clip sound apply where they can: a song draws no star (yet) and stills have no sound of their own
        if inp.character and inp.format != "song":
            image = reg.model(inp.image_model)
            if image is not None and frame_mode(image) is None:
                errors.append(f"{image.name} can't draw from reference pictures; pick an image model that can, "
                              "so the star looks the same in every shot")
        return errors

    def _blocks_guess(self, inp: FacelessInput) -> tuple[int, int]:
        """(blocks, characters of narration) expected before the plan exists."""
        chars = len(inp.script) if inp.script else round(inp.minutes * WORDS_PER_MINUTE * 6)
        seconds = chars / CHARS_PER_SECOND
        return max(1, math.ceil(seconds / block_seconds(inp.format))), chars

    def jobs(self, inp: FacelessInput, reg: Registry) -> list[PlannedJob]:
        if inp.format == "song":
            return self._song_jobs(inp, reg)
        n, chars = self._blocks_guess(inp)
        voice = reg.models[inp.voice_model]
        image = reg.models[inp.image_model]
        out = [PlannedJob(voice.id, mode_with_op(voice, "tts").id, inp.voice, prompt_chars=math.ceil(chars / n))
               for _ in range(n)]
        pic, roles = self._picture_mode(inp, reg)  # priced as if every shot shows the star
        out += [PlannedJob(image.id, pic.id, self._image_params(inp, reg, pic), roles) for _ in range(n)]
        if inp.format == "motion":
            video = reg.models[inp.video_model]
            mode = start_frame_mode(video)
            seconds = chars / CHARS_PER_SECOND / n + GAP_S
            out += [PlannedJob(video.id, mode.id, self._clip_params(inp, reg, seconds), ("start_image",))
                    for _ in range(n)]
        if inp.music:
            music = reg.models[inp.music_model]
            out.append(PlannedJob(music.id, mode_with_op(music, "music").id,
                                  music_params(reg.models[inp.music_model], chars / CHARS_PER_SECOND + n * GAP_S)))
        if inp.thumbnail:
            out.append(PlannedJob(image.id, pic.id, self._thumb_params(inp, reg, pic), roles))
        return out

    def _song_jobs(self, inp: FacelessInput, reg: Registry) -> list[PlannedJob]:
        """Before the lyrics exist: about four lines a section, a line every four seconds."""
        if inp.script and inp.script.strip():
            sections = [list(sec.lines) for sec in song_from_text(inp.script, "").sections]
        else:
            lines = song_lines(inp.minutes)
            n = min(MAX_SECTIONS, max(1, math.ceil(lines / LINES_PER_SECTION)))
            sections = [["la"] * (lines // n + (i < lines % n)) for i in range(n)]
        image, music = reg.models[inp.image_model], reg.models[inp.music_model]
        seconds = self._song_seconds(sections, reg, inp)
        out = [PlannedJob(image.id, text_mode(image).id, self._image_params(inp, reg)) for _ in sections]
        out.append(PlannedJob(music.id, mode_with_op(music, "song").id, {"sections": [
            {"name": f"Part {i}", "lines": lines, "duration": d}
            for i, (lines, d) in enumerate(zip(sections, seconds, strict=True), 1)]}))
        if inp.thumbnail:
            out.append(PlannedJob(image.id, text_mode(image).id, self._thumb_params(inp, reg)))
        return out

    def _song_seconds(self, sections: list[list[str]], reg: Registry, inp: FacelessInput) -> list[float]:
        """Each section's length, fitted within the longest song the music model makes."""
        limit = getattr(reg.models[inp.music_model].param("duration"), "max", None) or MAX_SONG_S
        return fit_song([section_seconds(lines) for lines in sections], float(limit))

    # ---------------------------------------------------------------- params per model

    def _picture_mode(self, inp: FacelessInput, reg: Registry) -> tuple[Mode, tuple[str, ...]]:
        """The image mode for a shot (and the input roles it fills): from the star's pictures when there is one."""
        spec = reg.models[inp.image_model]
        if inp.character and inp.format != "song" and (refs := frame_mode(spec)) is not None:
            return refs, (_ref_role(spec),)
        return text_mode(spec), ()  # type: ignore[return-value]

    def _image_params(self, inp: FacelessInput, reg: Registry, mode: Mode | None = None) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        mode_id = (mode or text_mode(spec)).id  # type: ignore[union-attr]
        return with_param(spec, mode_id, dict(inp.image_params), "aspect_ratio", inp.aspect_ratio)

    def _thumb_params(self, inp: FacelessInput, reg: Registry, mode: Mode | None = None) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        mode_id = (mode or text_mode(spec)).id  # type: ignore[union-attr]
        return with_param(spec, mode_id, self._image_params(inp, reg, mode), "num_images", 3)

    def _clip_params(self, inp: FacelessInput, reg: Registry, seconds: float) -> dict[str, Any]:
        spec = reg.models[inp.video_model]
        params = clip_params(spec, inp.aspect_ratio, seconds, inp.video_params)
        if inp.clip_sound and inp.format == "motion":  # priced too: native audio costs more on some models (Kling)
            params = with_param(spec, start_frame_mode(spec).id, params, "generate_audio", True)  # type: ignore[union-attr]
        return params

    # ---------------------------------------------------------------- the plan

    def _script(self, inp: FacelessInput, settled: Settled) -> tuple[str, str] | None:
        if inp.script and inp.script.strip():
            return inp.script.strip(), (inp.title or inp.topic or "Untitled").strip()
        out = ok(settled, "script")
        return (out["script"], (inp.title or out["title"]).strip()) if out else None

    def plan(self, inp: FacelessInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        cat = get_catalog()
        theme, style = cat.theme(inp.theme), cat.style(inp.style)
        assert theme is not None and style is not None
        specs: list[StepSpec] = []
        song = inp.format == "song"
        if not (inp.script and inp.script.strip()):
            specs.append(StepSpec("script", StepKind.llm, "Write the song" if song else "Write the script", {
                "op": "faceless_song" if song else "faceless_script", "topic": inp.topic, "theme": theme.id,
                "minutes": inp.minutes, "language": inp.language, "notes": inp.notes,
                **({"character": inp.character} if inp.character and not song else {})}))
        written = self._script(inp, settled)
        if written is None:
            return specs
        script, title = written
        before_plan = tuple(s.key for s in specs)
        if inp.review_script:
            specs.append(StepSpec("review", StepKind.checkpoint, "Approve the lyrics" if song else "Approve the script", {
                "type": "approve",
                "options": [{"value": "approve", "label": "Use these lyrics" if song else "Use this script"}],
                "script": script, "title": title}, before_plan))
            if ok(settled, "review") is None:
                return specs
            before_plan = ("review",)
        if song:
            lyrics = self._song(inp, settled, title)
            return specs + (self._song_steps(inp, settled, reg, lyrics, title, before_plan) if lyrics else [])
        specs.append(StepSpec("plan", StepKind.llm, "Plan the visuals", {
            "op": "faceless_plan", "script": script, "title": title, "style": style.id, "format": inp.format,
            **({"character": inp.character} if inp.character else {})}, before_plan))
        plan = ok(settled, "plan")
        if plan is None:
            return specs

        blocks: list[dict[str, Any]] = plan["blocks"]
        voice_spec, image_spec = reg.models[inp.voice_model], reg.models[inp.image_model]
        tts, t2i = mode_with_op(voice_spec, "tts"), text_mode(image_spec)
        assert tts is not None and t2i is not None
        voice_params = dict(inp.voice)
        if "voice_id" not in voice_params and voice_spec.param("voice_id") is not None:
            voice_params["voice_id"] = theme.voice_id
        for i, block in enumerate(blocks, 1):
            specs.append(StepSpec(voice_key(i), StepKind.generate, f"Narration {i}", {
                "model_id": voice_spec.id, "mode": tts.id, "prompt": block["narration"], "params": voice_params,
                "media": []}, ("plan",)))
            stars = bool(inp.character and block.get("star"))
            pic = self._picture_mode(inp, reg)[0] if stars else t2i
            visual = starring(block["visual"], inp.character) if stars and inp.character else block["visual"]
            specs.append(StepSpec(still_key(i), StepKind.generate, f"Image {i}", {
                "model_id": image_spec.id, "mode": pic.id, "params": self._image_params(inp, reg, pic), "media": [],
                "prompt": visual_prompt(visual, plan.get("roster", []), plan.get("world", ""), style.prompt),
            }, ("plan",)))
            if inp.format == "motion":
                still, voice = ok(settled, still_key(i)), ok(settled, voice_key(i))
                if still and voice:
                    video = reg.models[inp.video_model]
                    seconds = (voice.get("duration_ms") or 0) / 1000 + GAP_S
                    motion = block.get("motion") or "subtle natural motion, slow camera drift"
                    sound = f". {CLIP_SOUND_PROMPT}" if inp.clip_sound else ""
                    shot = named(block["visual"], plan.get("star")).strip().rstrip(".")
                    specs.append(StepSpec(clip_key(i), StepKind.generate, f"Clip {i}", {
                        "model_id": video.id, "mode": start_frame_mode(video).id,
                        "prompt": f"{shot}. {motion}{sound}",
                        "params": self._clip_params(inp, reg, seconds),
                        "media": [{"role": "start_image", "asset_id": still["asset_id"]}],
                    }, (still_key(i), voice_key(i)), optional=True))  # a failed clip falls back to its still

        specs += self._thumb_steps(inp, settled, reg, title, plan.get("world", ""), style.prompt, ("plan",),
                                   star=inp.character)

        voices = [ok(settled, voice_key(i)) for i in range(1, len(blocks) + 1)]
        if not all(voices):
            return specs
        voice_keys = tuple(voice_key(i) for i in range(1, len(blocks) + 1))
        if inp.music:
            total = sum((v.get("duration_ms") or 0) / 1000 + GAP_S for v in voices if v)
            music_spec = reg.models[inp.music_model]
            specs.append(StepSpec("music", StepKind.generate, "Music", {
                "model_id": music_spec.id, "mode": mode_with_op(music_spec, "music").id, "prompt": theme.music,
                "params": music_params(reg.models[inp.music_model], total), "media": []}, voice_keys, optional=True))

        visuals = [self._visual(inp, settled, i) for i in range(1, len(blocks) + 1)]
        if not all(visuals) or (inp.music and "music" not in settled):
            return specs
        music = ok(settled, "music") if inp.music else None
        deps = (*voice_keys, *(still_key(i) for i in range(1, len(blocks) + 1)),
                *((clip_key(i) for i in range(1, len(blocks) + 1)) if inp.format == "motion" else ()),
                *(("music",) if inp.music else ()))
        specs.append(StepSpec("cut", StepKind.local, "Assemble the episode", {
            "op": "narrated_cut", "title": title, "aspect_ratio": inp.aspect_ratio, "resolution": inp.resolution,
            "blocks": [{"visual": vis, "voice": v["asset_id"]} for vis, v in zip(visuals, voices, strict=True) if v],
            "music": music["asset_id"] if music else None,
            **({"clip_sound_db": CLIP_SOUND_DB} if inp.clip_sound and inp.format == "motion" else {})}, deps))
        cut = ok(settled, "cut")
        if inp.captions and cut:
            specs.append(StepSpec("captions", StepKind.local, "Burn in captions", {
                "op": "captions", "video": cut["asset_id"], "voices": [v["asset_id"] for v in voices if v],
                "narrations": [b["narration"] for b in blocks], "starts": cut["starts"], "style": style.id,
                "title": title}, ("cut",)))
        return specs

    # ---------------------------------------------------------------- the song format

    def _song(self, inp: FacelessInput, settled: Settled, title: str) -> Song | None:
        if inp.script and inp.script.strip():
            return song_from_text(inp.script, title)
        out = ok(settled, "script")
        return Song.model_validate(out["song"]) if out and out.get("song") else None

    def _song_steps(self, inp: FacelessInput, settled: Settled, reg: Registry, song: Song, title: str,
                    after: tuple[str, ...]) -> list[StepSpec]:
        cat = get_catalog()
        theme, style = cat.theme(inp.theme), cat.style(inp.style)
        assert theme is not None and style is not None
        image_spec, music_spec = reg.models[inp.image_model], reg.models[inp.music_model]
        t2i, sing = text_mode(image_spec), mode_with_op(music_spec, "song")
        assert t2i is not None and sing is not None
        seconds = self._song_seconds([s.lines for s in song.sections], reg, inp)
        specs = [StepSpec(still_key(i), StepKind.generate, f"Picture {i}: {sec.name}", {
            "model_id": image_spec.id, "mode": t2i.id, "params": self._image_params(inp, reg), "media": [],
            "prompt": visual_prompt(sec.picture, [], song.world, style.prompt)}, after)
            for i, sec in enumerate(song.sections, 1)]
        # your own lyrics come without a sound of their own; Claude's say how they should sound
        specs.append(StepSpec("sing", StepKind.generate, "Sing the song", {
            "model_id": music_spec.id, "mode": sing.id, "prompt": ", ".join(x for x in (song.music, theme.music) if x),
            "params": {"sections": [{"name": s.name, "lines": s.lines, "duration": d}
                                    for s, d in zip(song.sections, seconds, strict=True)]},
            "media": []}, after))
        specs += self._thumb_steps(inp, settled, reg, title, song.world, style.prompt, after)

        sung = ok(settled, "sing")
        if sung is None:
            return specs
        specs.append(StepSpec("parts", StepKind.local, "Cut the song at its sections", {
            "op": "split_audio", "audio": sung["asset_id"], "seconds": seconds, "title": title}, ("sing",)))
        parts = (ok(settled, "parts") or {}).get("asset_ids")
        stills = [ok(settled, still_key(i)) for i in range(1, len(song.sections) + 1)]
        if not parts or not all(stills):
            return specs
        specs.append(StepSpec("cut", StepKind.local, "Assemble the episode", {
            "op": "narrated_cut", "title": title, "aspect_ratio": inp.aspect_ratio, "resolution": inp.resolution,
            "blocks": [{"visual": s["asset_id"], "voice": part} for s, part in zip(stills, parts, strict=True) if s],
            "music": None, "gap_s": 0, "tail_s": 0.5},
            ("parts", *(still_key(i) for i in range(1, len(song.sections) + 1)))))
        cut = ok(settled, "cut")
        if inp.captions and cut:
            specs.append(StepSpec("captions", StepKind.local, "Burn in the lyrics", {
                "op": "captions", "video": cut["asset_id"], "voices": parts, "lead_in": SECTION_PAD_S,
                "sung": [s.lines for s in song.sections], "starts": cut["starts"],
                "style": style.id, "title": title}, ("cut",)))
        return specs

    def _thumb_steps(self, inp: FacelessInput, settled: Settled, reg: Registry, title: str, world: str, style: str,
                     after: tuple[str, ...], star: str | None = None) -> list[StepSpec]:
        if not inp.thumbnail:
            return []
        image_spec = reg.models[inp.image_model]
        pic = self._picture_mode(inp, reg)[0] if star else text_mode(image_spec)
        specs = [StepSpec("thumbs", StepKind.generate, "Thumbnails", {
            "model_id": image_spec.id, "mode": pic.id, "params": self._thumb_params(inp, reg, pic),  # type: ignore[union-attr]
            "media": [], "prompt": thumbnail_prompt(title, world, style, star)}, after, optional=True)]
        thumbs = ok(settled, "thumbs")
        if thumbs and len(thumbs.get("asset_ids", [])) > 1:
            specs.append(StepSpec("thumb", StepKind.checkpoint, "Pick a thumbnail", {
                "type": "pick_one", "options": [{"value": a, "label": f"Thumbnail {n}"}
                                                for n, a in enumerate(thumbs["asset_ids"], 1)]}, ("thumbs",)))
        return specs

    def _visual(self, inp: FacelessInput, settled: Settled, i: int) -> str | None:
        """The asset shown for block i: its clip, else its still once the clip has settled (or isn't wanted)."""
        still = ok(settled, still_key(i))
        if inp.format == "motion":
            if clip_key(i) not in settled:
                return None
            clip = ok(settled, clip_key(i))
            if clip:
                return clip["asset_id"]
        return still["asset_id"] if still else None

    def result(self, inp: FacelessInput, settled: Settled) -> dict[str, Any] | None:
        cut = ok(settled, "cut")
        if cut is None:
            return None
        final = ok(settled, "captions") if inp.captions else cut
        if final is None:
            return None
        thumbnail = None
        if inp.thumbnail:
            if "thumbs" not in settled:
                return None
            thumbs = ok(settled, "thumbs")
            if thumbs and len(thumbs.get("asset_ids", [])) > 1:
                pick = ok(settled, "thumb")
                if pick is None:
                    return None
                thumbnail = pick["choice"]
            elif thumbs:
                thumbnail = thumbs["asset_id"]
        return {"asset_id": final["asset_id"], "master_asset_id": cut["asset_id"],
                "captions_asset_id": final.get("subtitle_asset_id"), "thumbnail_asset_id": thumbnail}


def _ref_role(spec: ModelSpec) -> str:
    return (spec.elements or {}).get("ref_role", "image_references")
