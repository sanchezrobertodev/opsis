"""Faceless video: a topic (or your script) becomes a narrated, captioned episode with music.

Steps, keyed so edits only redo what they touch:
  script (Claude) -> review (approve the script) -> plan (Claude: blocks of narration + visuals)
  -> per block: voice[b01] (TTS), still[b01] (image), clip[b01] (animate the still; motion format only)
  -> music (optional) -> cut (assemble) -> captions (optional) ; thumbs -> thumb pick (optional)

Two formats: `stills` holds one image per spoken beat with a slow Ken Burns move; `motion` animates each
block's still into a clip as long as its narration. A clip that fails (moderation, say) falls back to its
still, so one bad block never sinks the episode. Models are chosen by what they do (tts, t2i, start frame,
music), so any registry model with those modes works.
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import WORDS_PER_MINUTE, block_seconds
from studio.media.assemble import GAP_S
from studio.media.speech import CHARS_PER_SECOND
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    fit_duration,
    mode_with_op,
    start_frame_mode,
    text_mode,
    with_param,
)
from studio.registry.loader import Registry


class FacelessInput(BaseModel):
    channel_id: uuid.UUID | None = None
    topic: str = Field("", max_length=300)
    script: str | None = Field(None, max_length=20000, description="Your own narration; skips writing one")
    title: str | None = Field(None, max_length=120)
    theme: str = "storytelling"
    style: str = "watercolor-chronicle"
    format: Literal["stills", "motion"] = "stills"
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


def thumbnail_prompt(title: str, world: str, style: str) -> str:
    return (f'Eye-catching video thumbnail for "{title}". One bold focal subject, simple uncluttered composition, '
            f"strong contrast, the title in large clean lettering. {world.strip().rstrip('.')}. {style.strip()}")


class FacelessRecipe(Recipe):
    id = "faceless"
    name = "Faceless video"
    summary = "A narrated, captioned video from a topic or your script."
    Input = FacelessInput

    def title(self, inp: FacelessInput) -> str:
        return f"Faceless: {(inp.title or inp.topic or (inp.script or '')[:60]).strip()[:100]}"

    # ---------------------------------------------------------------- checks and estimate

    def validate(self, inp: FacelessInput, reg: Registry) -> list[str]:
        cat = get_catalog()
        errors = []
        if cat.theme(inp.theme) is None:
            errors.append(f"unknown theme {inp.theme!r}")
        if cat.style(inp.style) is None:
            errors.append(f"unknown style {inp.style!r}")
        if not inp.topic.strip() and not (inp.script or "").strip():
            errors.append("give a topic or paste a script")
        needs = [(inp.voice_model, "tts", "speak the narration"), (inp.image_model, None, "make images from text")]
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
        return errors

    def _blocks_guess(self, inp: FacelessInput) -> tuple[int, int]:
        """(blocks, characters of narration) expected before the plan exists."""
        chars = len(inp.script) if inp.script else round(inp.minutes * WORDS_PER_MINUTE * 6)
        seconds = chars / CHARS_PER_SECOND
        return max(1, math.ceil(seconds / block_seconds(inp.format))), chars

    def jobs(self, inp: FacelessInput, reg: Registry) -> list[PlannedJob]:
        n, chars = self._blocks_guess(inp)
        voice = reg.models[inp.voice_model]
        image = reg.models[inp.image_model]
        out = [PlannedJob(voice.id, mode_with_op(voice, "tts").id, inp.voice, prompt_chars=math.ceil(chars / n))
               for _ in range(n)]
        out += [PlannedJob(image.id, text_mode(image).id, self._image_params(inp, reg)) for _ in range(n)]
        if inp.format == "motion":
            video = reg.models[inp.video_model]
            mode = start_frame_mode(video)
            seconds = chars / CHARS_PER_SECOND / n + GAP_S
            out += [PlannedJob(video.id, mode.id, self._clip_params(inp, reg, seconds), ("start_image",))
                    for _ in range(n)]
        if inp.music:
            music = reg.models[inp.music_model]
            out.append(PlannedJob(music.id, mode_with_op(music, "music").id,
                                  self._music_params(inp, reg, chars / CHARS_PER_SECOND + n * GAP_S)))
        if inp.thumbnail:
            out.append(PlannedJob(image.id, text_mode(image).id, self._thumb_params(inp, reg)))
        return out

    # ---------------------------------------------------------------- params per model

    def _image_params(self, inp: FacelessInput, reg: Registry) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        mode = text_mode(spec).id
        return with_param(spec, mode, dict(inp.image_params), "aspect_ratio", inp.aspect_ratio)

    def _thumb_params(self, inp: FacelessInput, reg: Registry) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        return with_param(spec, text_mode(spec).id, self._image_params(inp, reg), "num_images", 3)

    def _clip_params(self, inp: FacelessInput, reg: Registry, seconds: float) -> dict[str, Any]:
        spec = reg.models[inp.video_model]
        mode = start_frame_mode(spec).id
        params = with_param(spec, mode, dict(inp.video_params), "aspect_ratio", inp.aspect_ratio)
        return with_param(spec, mode, params, "duration", fit_duration(spec, mode, seconds))

    def _music_params(self, inp: FacelessInput, reg: Registry, seconds: float) -> dict[str, Any]:
        spec = reg.models[inp.music_model]
        mode = mode_with_op(spec, "music").id
        return with_param(spec, mode, {}, "duration", fit_duration(spec, mode, seconds))

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
        if not (inp.script and inp.script.strip()):
            specs.append(StepSpec("script", StepKind.llm, "Write the script", {
                "op": "faceless_script", "topic": inp.topic, "theme": theme.id, "minutes": inp.minutes,
                "language": inp.language, "notes": inp.notes}))
        written = self._script(inp, settled)
        if written is None:
            return specs
        script, title = written
        before_plan = tuple(s.key for s in specs)
        if inp.review_script:
            specs.append(StepSpec("review", StepKind.checkpoint, "Approve the script", {
                "type": "approve", "options": [{"value": "approve", "label": "Use this script"}],
                "script": script, "title": title}, before_plan))
            if ok(settled, "review") is None:
                return specs
            before_plan = ("review",)
        specs.append(StepSpec("plan", StepKind.llm, "Plan the visuals", {
            "op": "faceless_plan", "script": script, "title": title, "style": style.id, "format": inp.format},
            before_plan))
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
            specs.append(StepSpec(still_key(i), StepKind.generate, f"Image {i}", {
                "model_id": image_spec.id, "mode": t2i.id, "params": self._image_params(inp, reg), "media": [],
                "prompt": visual_prompt(block["visual"], plan.get("roster", []), plan.get("world", ""), style.prompt),
            }, ("plan",)))
            if inp.format == "motion":
                still, voice = ok(settled, still_key(i)), ok(settled, voice_key(i))
                if still and voice:
                    video = reg.models[inp.video_model]
                    seconds = (voice.get("duration_ms") or 0) / 1000 + GAP_S
                    motion = block.get("motion") or "subtle natural motion, slow camera drift"
                    specs.append(StepSpec(clip_key(i), StepKind.generate, f"Clip {i}", {
                        "model_id": video.id, "mode": start_frame_mode(video).id,
                        "prompt": f"{block['visual'].strip().rstrip('.')}. {motion}",
                        "params": self._clip_params(inp, reg, seconds),
                        "media": [{"role": "start_image", "asset_id": still["asset_id"]}],
                    }, (still_key(i), voice_key(i)), optional=True))  # a failed clip falls back to its still

        if inp.thumbnail:
            specs.append(StepSpec("thumbs", StepKind.generate, "Thumbnails", {
                "model_id": image_spec.id, "mode": t2i.id, "params": self._thumb_params(inp, reg), "media": [],
                "prompt": thumbnail_prompt(title, plan.get("world", ""), style.prompt)}, ("plan",), optional=True))
            thumbs = ok(settled, "thumbs")
            if thumbs and len(thumbs.get("asset_ids", [])) > 1:
                specs.append(StepSpec("thumb", StepKind.checkpoint, "Pick a thumbnail", {
                    "type": "pick_one", "options": [{"value": a, "label": f"Thumbnail {n}"}
                                                    for n, a in enumerate(thumbs["asset_ids"], 1)]}, ("thumbs",)))

        voices = [ok(settled, voice_key(i)) for i in range(1, len(blocks) + 1)]
        if not all(voices):
            return specs
        voice_keys = tuple(voice_key(i) for i in range(1, len(blocks) + 1))
        if inp.music:
            total = sum((v.get("duration_ms") or 0) / 1000 + GAP_S for v in voices if v)
            music_spec = reg.models[inp.music_model]
            specs.append(StepSpec("music", StepKind.generate, "Music", {
                "model_id": music_spec.id, "mode": mode_with_op(music_spec, "music").id, "prompt": theme.music,
                "params": self._music_params(inp, reg, total), "media": []}, voice_keys, optional=True))

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
            "music": music["asset_id"] if music else None}, deps))
        cut = ok(settled, "cut")
        if inp.captions and cut:
            specs.append(StepSpec("captions", StepKind.local, "Burn in captions", {
                "op": "captions", "video": cut["asset_id"], "voices": [v["asset_id"] for v in voices if v],
                "narrations": [b["narration"] for b in blocks], "starts": cut["starts"], "style": style.id,
                "title": title}, ("cut",)))
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
