"""Music video: your song becomes a cut of shots that follow its lyrics, optionally starring someone from a picture.

  transcribe (speech-to-text: every sung word with its time) -> shots (cut the song at the pauses in its lyrics)
  -> plan (Claude: one shot per section from its lyrics, and the video's world)
  -> per shot: frame[sNN] (their picture as the reference, or from text) -> clip[sNN] (animated, as long as its section)
  -> parts (the song cut at the shots) -> cut (shots back to back over the song) -> captions (the lyrics, word-timed)

A clip that fails falls back to its picture, so one refused shot never sinks the video. A song with no words heard
(an instrumental) still gets its shots, from the idea alone, and no captions.
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.db.models import StepKind
from studio.faceless.catalog import get_catalog
from studio.llm.client import LLMError, LLMResult, structured
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    fit_duration,
    mode_with_op,
    start_frame_mode,
    text_mode,
    with_param,
)
from studio.pipelines.recipes.vlog import frame_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

TARGET_S, MIN_S = 6.0, 3.0  # a shot's length: about six seconds, at least three
MAX_SHOT_S = 10.0  # the longest clip we ask for, whatever the video model allows
SONG_GUESS_S = 180  # the estimate before the song is measured: a three-minute song


class MusicVideoInput(BaseModel):
    song: uuid.UUID = Field(description="The song (an audio file or a clip with sound)")
    idea: str = Field("", max_length=500, description="What the video is about or how it looks")
    character_image: uuid.UUID | None = Field(None, description="Someone to star in every shot")
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: Literal["720p", "1080p"] = "1080p"
    max_shots: int = Field(24, ge=4, le=40)
    transcribe_model: str = "scribe-v2"
    image_model: str = "nano-banana-2"
    video_model: str = "ltx-2.5"
    captions: bool = True
    caption_style: str = "editorial-motion-graphics"


# ---------------------------------------------------------------- the shot plan (Claude)


class MusicShot(BaseModel):
    visual: str = Field(description="One concrete shot: subject, action, setting, framing. No art-style words")
    motion: str = Field(description="How the shot moves: camera and subject motion")


class MusicPlan(BaseModel):
    world: str = Field(description="One sentence fixing the video's look: place, era, palette, light")
    shots: list[MusicShot]


PLAN_SYSTEM = """You direct music videos. You get the song's sections in order, each with the lyrics sung over it
(or nothing for an instrumental part), and plan one shot per section.

- Follow the lyrics: show what they're about, literally or as a clear visual metaphor; instrumental parts carry the
  mood. Build a loose story across the video and vary the framing (wide, medium, close-up, overhead).
- `visual` is concrete: subject, action, setting, framing. Never mention an art style or rendering. No text, letters
  or logos in the picture.
- When a star is given, they appear in most shots; write "the star" for them and never describe their face.
- Return exactly one shot per section, in order. `world` pins the look of the whole video in one sentence."""


async def plan_shots(session: AsyncSession, user_id: uuid.UUID, *, sections: list[str], idea: str,
                     star: bool) -> LLMResult[MusicPlan]:
    listed = "\n".join(f"Section {i}: {text or '(instrumental)'}" for i, text in enumerate(sections, 1))
    prompt = "\n".join(line for line in [
        f"Idea: {idea}" if idea else "",
        "A star appears: the person from a reference photo." if star else "No star: show the world of the song.",
        f"\n{listed}",
    ] if line)
    result = await structured(
        session, user_id, purpose="music_video.plan", model=get_settings().llm_smart_model, system=PLAN_SYSTEM,
        prompt=prompt, output=MusicPlan, mock=lambda: _mock_plan(sections, star), effort="medium", max_tokens=8000)
    if len(result.output.shots) != len(sections):
        raise LLMError(f"Claude planned {len(result.output.shots)} shots for {len(sections)} sections. Try again.",
                       retryable=True)
    return result


def _mock_plan(sections: list[str], star: bool) -> MusicPlan:
    framings = ["Wide shot", "Medium shot", "Close-up", "Overhead view"]
    who = "the star" if star else "a city street"
    return MusicPlan(world="A rain-washed city at night, neon reflections, deep blues and warm pinks.", shots=[
        MusicShot(visual=f"{framings[i % 4]} of {who}: {(text or 'the beat drops')[:60]}", motion="slow push in")
        for i, text in enumerate(sections)])


# ---------------------------------------------------------------- the recipe


def frame_key(i: int) -> str:
    return f"frame[s{i:02d}]"


def clip_key(i: int) -> str:
    return f"clip[s{i:02d}]"


def _picture_mode(spec: ModelSpec, star: bool) -> Mode | None:
    return frame_mode(spec) if star else (text_mode(spec) if spec.output_type == "image" else None)


class MusicVideoRecipe(Recipe):
    id = "music_video"
    name = "Music video"
    summary = "Your song as a music video that follows its lyrics, optionally starring someone from a picture."
    Input = MusicVideoInput

    def title(self, inp: MusicVideoInput) -> str:
        return f"Music video: {inp.idea.strip()[:80] or 'your song'}"

    def validate(self, inp: MusicVideoInput, reg: Registry) -> list[str]:
        errors = []
        if inp.captions and get_catalog().style(inp.caption_style) is None:
            errors.append(f"unknown caption style {inp.caption_style!r}")
        star = inp.character_image is not None
        checks = [
            (inp.transcribe_model, lambda s: mode_with_op(s, "transcribe"), "hear the lyrics"),
            (inp.image_model, lambda s: _picture_mode(s, star),
             "draw a picture from a reference photo" if star else "draw a picture from text"),
            (inp.video_model, start_frame_mode, "animate a picture"),
        ]
        for model_id, find, what in checks:
            spec = reg.model(model_id)
            if spec is None:
                errors.append(f"unknown model {model_id!r}")
            elif find(spec) is None:
                errors.append(f"{spec.name} can't {what}")
        return errors

    def _ref_role(self, reg: Registry, inp: MusicVideoInput) -> str:
        return (reg.models[inp.image_model].elements or {}).get("ref_role", "image_references")

    def _max_s(self, inp: MusicVideoInput, reg: Registry) -> float:
        p = reg.models[inp.video_model].param("duration")
        return min(MAX_SHOT_S, float(getattr(p, "max", None) or MAX_SHOT_S))

    def _frame_params(self, inp: MusicVideoInput, reg: Registry) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        mode = _picture_mode(spec, inp.character_image is not None)
        assert mode is not None
        return with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def _clip_params(self, inp: MusicVideoInput, reg: Registry, seconds: float) -> dict[str, Any]:
        spec = reg.models[inp.video_model]
        mode = start_frame_mode(spec).id  # type: ignore[union-attr]
        params = with_param(spec, mode, {}, "aspect_ratio", inp.aspect_ratio)
        return with_param(spec, mode, params, "duration", fit_duration(spec, mode, seconds))

    def jobs(self, inp: MusicVideoInput, reg: Registry) -> list[PlannedJob]:
        """The song's length is only known once it's measured, so this prices a three-minute song (the transcript
        itself at the song's real length)."""
        heard, image, video = (reg.models[m] for m in (inp.transcribe_model, inp.image_model, inp.video_model))
        target = max(TARGET_S, SONG_GUESS_S / inp.max_shots)
        n = min(inp.max_shots, math.ceil(SONG_GUESS_S / target))
        pic = _picture_mode(image, inp.character_image is not None)
        assert pic is not None
        refs = ((self._ref_role(reg, inp), str(inp.character_image)),) if inp.character_image else ()
        out = [PlannedJob(heard.id, mode_with_op(heard, "transcribe").id, {}, ("input_audio",),  # type: ignore[union-attr]
                          media=(("input_audio", str(inp.song)),))]
        out += [PlannedJob(image.id, pic.id, self._frame_params(inp, reg), tuple(r for r, _ in refs), media=refs)
                for _ in range(n)]
        out += [PlannedJob(video.id, start_frame_mode(video).id, self._clip_params(inp, reg, target),  # type: ignore[union-attr]
                           ("start_image",)) for _ in range(n)]
        return out

    def plan(self, inp: MusicVideoInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        heard = reg.models[inp.transcribe_model]
        specs = [StepSpec("transcribe", StepKind.generate, "Hear the lyrics", {
            "model_id": heard.id, "mode": mode_with_op(heard, "transcribe").id, "prompt": "",  # type: ignore[union-attr]
            "params": {}, "media": [{"role": "input_audio", "asset_id": str(inp.song)}]})]
        script = ok(settled, "transcribe")
        if script is None:
            return specs
        specs.append(StepSpec("shots", StepKind.local, "Cut the song into shots", {
            "op": "lyric_shots", "audio": str(inp.song), "transcript": script["asset_id"], "max_shots": inp.max_shots,
            "target_s": TARGET_S, "min_s": MIN_S, "max_s": self._max_s(inp, reg)}, ("transcribe",)))
        cut = ok(settled, "shots")
        if cut is None:
            return specs
        sections: list[dict[str, Any]] = cut["shots"]
        specs.append(StepSpec("plan", StepKind.llm, "Plan the shots", {
            "op": "music_video_plan", "sections": [s["text"] for s in sections], "idea": inp.idea,
            "star": inp.character_image is not None}, ("shots",)))
        specs.append(StepSpec("parts", StepKind.local, "Cut the song at the shots", {
            "op": "split_audio", "audio": str(inp.song), "seconds": cut["seconds"], "title": "Music video"},
            ("shots",)))
        plan = ok(settled, "plan")
        if plan is None:
            return specs

        image, video = reg.models[inp.image_model], reg.models[inp.video_model]
        pic, i2v = _picture_mode(image, inp.character_image is not None), start_frame_mode(video)
        assert pic and i2v
        media = ([{"role": self._ref_role(reg, inp), "asset_id": str(inp.character_image)}]
                 if inp.character_image else [])
        star = " The star is the person from the reference photo, same face, hair and build." if media else ""
        for i, (shot, section) in enumerate(zip(plan["shots"], sections, strict=True), 1):
            specs.append(StepSpec(frame_key(i), StepKind.generate, f"Shot {i}: picture", {
                "model_id": image.id, "mode": pic.id, "params": self._frame_params(inp, reg), "media": media,
                "prompt": f"{shot['visual'].strip().rstrip('.')}. {plan['world'].strip().rstrip('.')}.{star} "
                          "Cinematic music video still, no text or logos."}, ("plan",)))
            frame = ok(settled, frame_key(i))
            if frame:  # a failed clip falls back to its picture
                specs.append(StepSpec(clip_key(i), StepKind.generate, f"Shot {i}: motion", {
                    "model_id": video.id, "mode": i2v.id,
                    "prompt": f"{shot['visual'].strip().rstrip('.')}. {shot['motion']}",
                    "params": self._clip_params(inp, reg, section["end"] - section["start"]),
                    "media": [{"role": "start_image", "asset_id": frame["asset_id"]}]}, (frame_key(i),), optional=True))

        parts = (ok(settled, "parts") or {}).get("asset_ids")
        visuals = [self._visual(settled, i) for i in range(1, len(sections) + 1)]
        if not parts or not all(visuals):
            return specs
        specs.append(StepSpec("cut", StepKind.local, "Assemble the video", {
            "op": "narrated_cut", "title": "Music video", "aspect_ratio": inp.aspect_ratio,
            "resolution": inp.resolution, "music": None, "gap_s": 0, "tail_s": 0,
            "blocks": [{"visual": v, "voice": p} for v, p in zip(visuals, parts, strict=True)]},
            ("parts", *(frame_key(i) for i in range(1, len(sections) + 1)),
             *(clip_key(i) for i in range(1, len(sections) + 1)))))
        done = ok(settled, "cut")
        if inp.captions and cut["lyrics"] and done:
            specs.append(StepSpec("captions", StepKind.local, "Burn in the lyrics", {
                "op": "captions", "video": done["asset_id"], "voices": parts, "starts": done["starts"],
                "words": [s["words"] for s in sections], "style": inp.caption_style, "title": "Music video"},
                ("cut",)))
        return specs

    def _visual(self, settled: Settled, i: int) -> str | None:
        """Shot i's clip once it has settled, else its picture."""
        frame = ok(settled, frame_key(i))
        if frame is None or clip_key(i) not in settled:
            return None
        clip = ok(settled, clip_key(i))
        return clip["asset_id"] if clip else frame["asset_id"]

    def result(self, inp: MusicVideoInput, settled: Settled) -> dict[str, Any] | None:
        cut, shots = ok(settled, "cut"), ok(settled, "shots")
        if cut is None or shots is None:
            return None
        if inp.captions and shots["lyrics"]:
            final = ok(settled, "captions")
            if final is None:
                return None
            return {"asset_id": final["asset_id"], "master_asset_id": cut["asset_id"],
                    "captions_asset_id": final.get("subtitle_asset_id")}
        return {"asset_id": cut["asset_id"], "master_asset_id": cut["asset_id"], "captions_asset_id": None}
