"""Character vlog: a picture of someone plus an idea becomes a short talking vlog starring them.

  script (Claude: title + shots, each a line they say and what we see) -> review (approve)
  -> per shot: frame[sNN] (their picture as the reference) ; voice[sNN] (TTS of the line)
     -> on camera: lip[sNN] (the frame speaks the line)  |  cutaway: clip[sNN] (the frame animated, voice over it)
  -> music (optional) -> cut (assemble) -> captions (optional)

A lip-synced or animated shot that fails falls back to its frame with the voice over it, so one refused shot never
sinks the vlog. Models are picked by what they do (reference images, tts, start frame, lipsync, music).
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import StepKind
from studio.faceless.catalog import get_catalog
from studio.llm.client import LLMError, LLMResult, model_for, structured
from studio.media.assemble import GAP_S
from studio.media.speech import CHARS_PER_SECOND
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    clip_params,
    mode_with_op,
    music_params,
    start_frame_mode,
    with_param,
)
from studio.pipelines.recipes.keyframe import ref_mode
from studio.pipelines.recipes.talking import lipsync_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

# without this, a shot describing two moments comes back as a collage of panels
SINGLE_FRAME = "One single image of one moment, full frame: not a collage, grid, split screen or panels."
LINE_CHARS = 90  # about one spoken sentence (6 s), what the writer aims for per shot


SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"  # an Element's handle, as written after @


class VlogInput(BaseModel):
    character_image: uuid.UUID | None = Field(None, description="A clear picture of the person who stars in the vlog")
    character: str | None = Field(None, pattern=SLUG, description="Or a saved character's @handle: their pictures "
                                                                   "are the reference and their name is used")
    idea: str = Field(min_length=1, max_length=500, description="What the vlog is about")
    name: str = Field("", max_length=60, description="What the person is called, if they say it")
    shots: int = Field(5, ge=3, le=8)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: Literal["720p", "1080p"] = "1080p"
    language: str = Field("English", max_length=40)
    notes: str = Field("", max_length=1000, description="Guidance for the writer")
    voice_model: str = "eleven-v4"
    voice: dict[str, Any] = Field(default_factory=dict, description="Voice model params, e.g. voice_id")
    image_model: str = "nano-banana-2"
    video_model: str = "ltx-2.5"
    lipsync_model: str = "kling-avatar-2"
    music: bool = True
    music_model: str = "eleven-music"
    captions: bool = True
    caption_style: str = "editorial-motion-graphics"
    review_script: bool = Field(True, description="Stop for approval once the script is written")

    @model_validator(mode="after")
    def _one_star(self) -> VlogInput:
        if (self.character_image is None) == (self.character is None):
            raise ValueError("give a picture of the person or pick a saved character, not both")
        return self

    @property
    def refs(self) -> list[dict[str, str]]:
        """The picture to attach to every frame; a saved character's attach through their @mention instead."""
        return [{"role": "image_references", "asset_id": str(self.character_image)}] if self.character_image else []


# ---------------------------------------------------------------- the script (Claude)


class VlogShot(BaseModel):
    line: str = Field(description="What the person says during this shot: one or two short spoken sentences")
    visual: str = Field(description="What we see: where they are and what they're doing, framing. No art-style words")
    on_camera: bool = Field(description="True when they talk to the camera in this shot; false for a cutaway that "
                                        "shows something while their voice continues")
    motion: str = Field("", description="For cutaways: how the shot moves (camera and subject)")


class VlogScript(BaseModel):
    title: str = Field(description="The vlog's title, under 70 characters")
    shots: list[VlogShot]


VLOG_SYSTEM = """You write short personal vlogs: one person talks to their phone camera in first person, warm and
natural, like a creator on Shorts or Reels. Each shot is one or two short spoken sentences.

- Open with a hook in the first line and end with a sign-off.
- About half the shots are on camera (talking into the lens); the rest are cutaways that show what they talk about
  while their voice carries on. The first and last shots are on camera.
- `visual` is concrete: place, action, framing (selfie close-up, handheld medium, wide establishing). Never mention
  an art style. The person in every shot is the same one, seen in a reference photo; don't describe their face.
- `visual` is one moment, a single picture: never two actions or "then". Movement goes in `motion`.
- Only the spoken words go in `line`: no stage directions, emojis or hashtags."""


async def write_vlog(session: AsyncSession, user_id: uuid.UUID, *, idea: str, shots: int, name: str = "",
                     language: str = "English", notes: str = "") -> LLMResult[VlogScript]:
    prompt = "\n".join(line for line in [
        f"Idea: {idea}",
        f"The person is called {name}." if name else "",
        f"Shots: exactly {shots}, each line about {LINE_CHARS // 6} words.",
        f"Language: {language}.",
        f"Extra notes from the creator: {notes}" if notes else "",
    ] if line)
    result = await structured(
        session, user_id, purpose="vlog.script",
        model=await model_for(session, user_id, "smart"), system=VLOG_SYSTEM,
        prompt=prompt, output=VlogScript, mock=lambda: _mock_vlog(idea, shots), effort="medium", max_tokens=6000)
    kept = [s for s in result.output.shots if s.line.strip()]
    if len(kept) < shots:  # the estimate and the confirmation were for `shots`
        raise LLMError(f"Claude wrote {len(kept)} shots with lines instead of {shots}. Try again.", retryable=True)
    result.output.shots = kept[:shots]
    return result


def _mock_vlog(idea: str, shots: int) -> VlogScript:
    topic = idea.strip().rstrip(".").lower()
    out = [VlogShot(line=f"Hey everyone, today it's all about {topic}.", visual="Selfie close-up at home, morning light",
                    on_camera=True)]
    for i in range(1, shots - 1):
        cam = i % 2 == 0
        out.append(VlogShot(line=f"Part {i} of {topic}, and honestly it's better than I expected.",
                            visual=("Handheld medium shot, talking while walking" if cam else
                                    f"Cutaway showing part {i} of {topic} up close"),
                            on_camera=cam, motion="" if cam else "slow handheld push in"))
    out.append(VlogShot(line="That's it for today, see you in the next one!", visual="Selfie close-up, waving",
                        on_camera=True))
    return VlogScript(title=idea.strip()[:70], shots=out)


# ---------------------------------------------------------------- the recipe


def frame_key(i: int) -> str:
    return f"frame[s{i:02d}]"


def voice_key(i: int) -> str:
    return f"voice[s{i:02d}]"


def lip_key(i: int) -> str:
    return f"lip[s{i:02d}]"


def clip_key(i: int) -> str:
    return f"clip[s{i:02d}]"


def frame_prompt(shot: dict[str, Any], character: str | None = None) -> str:
    """A shot's picture: the person is the saved character's @mention (their pictures attach) or the attached photo."""
    who = ("talking straight into the camera lens, mouth relaxed, selfie framing" if shot["on_camera"]
           else "in the scene")
    person = f"@{character}" if character else "The person from the reference photo"
    return (f"{shot['visual'].strip().rstrip('.')}. {person}, same face, hair and build, "
            f"{who}. Natural vlog look, real light, phone camera. {SINGLE_FRAME}")


def frame_mode(spec: ModelSpec) -> Mode | None:
    """The image model's reference mode, if it draws pictures that natively take reference images."""
    if spec.output_type != "image" or (spec.elements or {}).get("strategy") != "refs_native":
        return None
    return ref_mode(spec)


class VlogRecipe(Recipe):
    id = "vlog"
    name = "Character vlog"
    summary = "A short talking vlog starring the person in your picture, from an idea."
    Input = VlogInput

    def title(self, inp: VlogInput) -> str:
        return f"Vlog: {inp.idea.strip()[:90]}"

    async def resolve(self, session: AsyncSession, user_id: uuid.UUID, inp: VlogInput, reg: Registry) -> VlogInput:
        """A saved character's own voice speaks when none was picked, written into the input so the estimate and
        the run use it."""
        if not inp.character or inp.voice:
            return inp
        from studio.db.models import Element, ElementKind  # local import: models import nothing from recipes

        saved = (await session.execute(select(Element.voice).where(
            Element.user_id == user_id, Element.slug == inp.character, Element.kind == ElementKind.character,
            Element.deleted_at.is_(None)))).scalar_one_or_none() or {}
        spec = reg.model(saved.get("model_id", ""))
        if spec is None or mode_with_op(spec, "tts") is None or not saved.get("params"):
            return inp
        return inp.model_copy(update={"voice_model": spec.id, "voice": dict(saved["params"])})

    def validate(self, inp: VlogInput, reg: Registry) -> list[str]:
        errors = []
        if get_catalog().style(inp.caption_style) is None:
            errors.append(f"unknown caption style {inp.caption_style!r}")
        checks = [
            (inp.image_model, frame_mode, "draw a picture from a reference photo"),
            (inp.voice_model, lambda s: mode_with_op(s, "tts"), "speak the lines"),
            (inp.video_model, start_frame_mode, "animate a picture"),
            (inp.lipsync_model, lambda s: lipsync_mode(s, "image"), "make a picture speak"),
        ]
        if inp.music:
            checks.append((inp.music_model, lambda s: mode_with_op(s, "music"), "make music"))
        for model_id, find, what in checks:
            spec = reg.model(model_id)
            if spec is None:
                errors.append(f"unknown model {model_id!r}")
            elif find(spec) is None:
                errors.append(f"{spec.name} can't {what}")
        return errors

    # ---------------------------------------------------------------- params

    def _frame_params(self, inp: VlogInput, reg: Registry) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        return with_param(spec, ref_mode(spec).id, {}, "aspect_ratio", inp.aspect_ratio)  # type: ignore[union-attr]

    def claude_calls(self, inp: VlogInput) -> list[str]:
        return ["vlog.script"]

    def jobs(self, inp: VlogInput, reg: Registry) -> list[PlannedJob]:
        """Before the script exists: a line of about LINE_CHARS a shot, half the shots on camera."""
        image, voice = reg.models[inp.image_model], reg.models[inp.voice_model]
        video, lips = reg.models[inp.video_model], reg.models[inp.lipsync_model]
        seconds = LINE_CHARS / CHARS_PER_SECOND
        on = math.ceil(inp.shots / 2)
        known = tuple((m["role"], m["asset_id"]) for m in inp.refs)
        out = [PlannedJob(image.id, ref_mode(image).id, self._frame_params(inp, reg), ("image_references",),  # type: ignore[union-attr]
                          media=known) for _ in range(inp.shots)]
        out += [PlannedJob(voice.id, mode_with_op(voice, "tts").id, dict(inp.voice), prompt_chars=LINE_CHARS)  # type: ignore[union-attr]
                for _ in range(inp.shots)]
        out += [PlannedJob(lips.id, lipsync_mode(lips, "image").id, {}, ("character_image", "input_audio"),  # type: ignore[union-attr]
                           planned_seconds=(("input_audio", seconds),)) for _ in range(on)]
        out += [PlannedJob(video.id, start_frame_mode(video).id, clip_params(reg.models[inp.video_model], inp.aspect_ratio, seconds + GAP_S),  # type: ignore[union-attr]
                           ("start_image",)) for _ in range(inp.shots - on)]
        if inp.music:
            music = reg.models[inp.music_model]
            out.append(PlannedJob(music.id, mode_with_op(music, "music").id,  # type: ignore[union-attr]
                                  music_params(reg.models[inp.music_model], inp.shots * (seconds + GAP_S))))
        return out

    # ---------------------------------------------------------------- the plan

    def plan(self, inp: VlogInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs = [StepSpec("script", StepKind.llm, "Write the vlog", {
            "op": "vlog_script", "idea": inp.idea, "shots": inp.shots, "name": inp.name, "character": inp.character,
            "language": inp.language,
            "notes": inp.notes})]
        script = ok(settled, "script")
        if script is None:
            return specs
        after: tuple[str, ...] = ("script",)
        if inp.review_script:
            specs.append(StepSpec("review", StepKind.checkpoint, "Approve the script", {
                "type": "approve", "options": [{"value": "approve", "label": "Use this script"}],
                "script": "\n\n".join(s["line"] for s in script["shots"]), "title": script["title"]}, after))
            if ok(settled, "review") is None:
                return specs
            after = ("review",)

        shots: list[dict[str, Any]] = script["shots"]
        image, voice = reg.models[inp.image_model], reg.models[inp.voice_model]
        voice_params = dict(inp.voice)
        video, lips = reg.models[inp.video_model], reg.models[inp.lipsync_model]
        refs, tts = ref_mode(image), mode_with_op(voice, "tts")
        i2v, sync = start_frame_mode(video), lipsync_mode(lips, "image")
        assert refs and tts and i2v and sync
        for i, shot in enumerate(shots, 1):
            specs.append(StepSpec(frame_key(i), StepKind.generate, f"Shot {i}: picture", {
                "model_id": image.id, "mode": refs.id, "prompt": frame_prompt(shot, inp.character),
                "params": self._frame_params(inp, reg),
                "media": [{**m, "role": (image.elements or {}).get("ref_role", m["role"])} for m in inp.refs]},
                after))
            specs.append(StepSpec(voice_key(i), StepKind.generate, f"Shot {i}: voice", {
                "model_id": voice.id, "mode": tts.id, "prompt": shot["line"], "params": voice_params,
                "media": []}, after))
            frame, spoken = ok(settled, frame_key(i)), ok(settled, voice_key(i))
            if not (frame and spoken):
                continue
            deps = (frame_key(i), voice_key(i))
            if shot["on_camera"]:  # a failed lip sync falls back to the picture with the voice over it
                specs.append(StepSpec(lip_key(i), StepKind.generate, f"Shot {i}: talking", {
                    "model_id": lips.id, "mode": sync.id,
                    "prompt": "A person talking naturally to their phone camera." if sync.prompt != "none" else "",
                    "params": {}, "media": [{"role": "character_image", "asset_id": frame["asset_id"]},
                                            {"role": "input_audio", "asset_id": spoken["asset_id"]}]},
                    deps, optional=True))
            else:
                seconds = (spoken.get("duration_ms") or 0) / 1000 + GAP_S
                specs.append(StepSpec(clip_key(i), StepKind.generate, f"Shot {i}: cutaway", {
                    "model_id": video.id, "mode": i2v.id,
                    "prompt": f"{shot['visual'].strip().rstrip('.')}. {shot.get('motion') or 'gentle handheld motion'}",
                    "params": clip_params(reg.models[inp.video_model], inp.aspect_ratio, seconds),
                    "media": [{"role": "start_image", "asset_id": frame["asset_id"]}]}, deps, optional=True))

        voices = [ok(settled, voice_key(i)) for i in range(1, len(shots) + 1)]
        if not all(voices):
            return specs
        voice_keys = tuple(voice_key(i) for i in range(1, len(shots) + 1))
        if inp.music:
            total = sum((v.get("duration_ms") or 0) / 1000 + GAP_S for v in voices if v)
            music = reg.models[inp.music_model]
            specs.append(StepSpec("music", StepKind.generate, "Music", {
                "model_id": music.id, "mode": mode_with_op(music, "music").id,  # type: ignore[union-attr]
                "prompt": "light, upbeat lifestyle vlog background music, no vocals",
                "params": music_params(reg.models[inp.music_model], total), "media": []}, voice_keys, optional=True))

        blocks = [self._block(settled, i, shot) for i, shot in enumerate(shots, 1)]
        if not all(blocks) or (inp.music and "music" not in settled):
            return specs
        music_out = ok(settled, "music") if inp.music else None
        deps = (*voice_keys, *(frame_key(i) for i in range(1, len(shots) + 1)),
                *((lip_key(i) if s["on_camera"] else clip_key(i)) for i, s in enumerate(shots, 1)),
                *(("music",) if inp.music else ()))
        specs.append(StepSpec("cut", StepKind.local, "Assemble the vlog", {
            "op": "narrated_cut", "title": script["title"], "aspect_ratio": inp.aspect_ratio,
            "resolution": inp.resolution, "music": music_out["asset_id"] if music_out else None,
            "blocks": [{**b, "voice": v["asset_id"]} for b, v in zip(blocks, voices, strict=True) if b and v]}, deps))
        cut = ok(settled, "cut")
        if inp.captions and cut:
            specs.append(StepSpec("captions", StepKind.local, "Burn in captions", {
                "op": "captions", "video": cut["asset_id"], "voices": [v["asset_id"] for v in voices if v],
                "narrations": [s["line"] for s in shots], "starts": cut["starts"], "style": inp.caption_style,
                "title": script["title"]}, ("cut",)))
        return specs

    def _block(self, settled: Settled, i: int, shot: dict[str, Any]) -> dict[str, Any] | None:
        """What plays during shot i once its talking or cutaway step has settled: that clip, else the picture."""
        key = lip_key(i) if shot["on_camera"] else clip_key(i)
        frame = ok(settled, frame_key(i))
        if key not in settled or frame is None:
            return None
        made = ok(settled, key)
        if made:  # a lip-synced clip must play at its own speed to stay in sync
            return {"visual": made["asset_id"], "hold": shot["on_camera"]}
        return {"visual": frame["asset_id"]}

    def result(self, inp: VlogInput, settled: Settled) -> dict[str, Any] | None:
        cut = ok(settled, "cut")
        final = (ok(settled, "captions") if inp.captions else cut) if cut else None
        if final is None:
            return None
        return {"asset_id": final["asset_id"], "master_asset_id": cut["asset_id"],  # type: ignore[index]
                "captions_asset_id": final.get("subtitle_asset_id")}
