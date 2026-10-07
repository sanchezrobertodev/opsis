"""Talking shot: a picture or a clip speaks a script (or recorded speech).

  voice (TTS, when there's a script) -> sync[i:model] (each chosen lipsync model) -> pick (if several)

Lipsync models are picked by what they do, never by id: the `lipsync` op on a mode that takes speech
(`input_audio`) plus a picture (`character_image`) or a clip (`input_video`). Editing the script redoes the voice
and the lipsync and nothing else. A Cinema shot's run names its project and shot, so each result is a take.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from studio.db.models import StepKind
from studio.media.speech import CHARS_PER_SECOND
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_with_op
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

SOURCE_ROLE = {"image": "character_image", "video": "input_video"}
DEFAULT_PROMPT = "A person speaking naturally to camera."


def lipsync_mode(spec: ModelSpec, source: Literal["image", "video"]) -> Mode | None:
    """The mode that makes `source` speak: the lipsync op, speech in, and the picture or clip as the one other
    required input."""
    role = SOURCE_ROLE[source]
    for m in spec.modes:
        slot = m.slot(role)
        if "lipsync" in m.ops and slot and slot.required and m.slot("input_audio") \
                and all(s.role in (role, "input_audio") or not s.required for s in m.medias):
            return m
    return None


def spoken_seconds_estimate(text: str) -> float:
    return max(1.0, len(text.strip()) / CHARS_PER_SECOND)


class LipsyncChoice(BaseModel):
    model_id: str
    params: dict[str, Any] = {}


class TalkingShotInput(BaseModel):
    image_asset_id: uuid.UUID | None = Field(None, description="A portrait, picture or character still to animate")
    video_asset_id: uuid.UUID | None = Field(None, description="Or a clip whose mouth is re-timed to the speech")
    script: str = Field("", max_length=5000, description="What they say, voiced with `voice`")
    audio_asset_id: uuid.UUID | None = Field(None, description="Or recorded speech instead of a script")
    voice_model: str = "eleven-v4"
    voice: dict[str, Any] = Field(default_factory=dict, description="Voice model params, e.g. voice_id")
    prompt: str = Field("", max_length=1000, description="How they move, for models that take a description")
    models: list[LipsyncChoice] = Field(min_length=1, max_length=3)
    project_id: uuid.UUID | None = None
    shot_key: str | None = Field(None, pattern=r"^[a-z][a-z0-9]{2,15}$")

    @model_validator(mode="after")
    def _one_of_each(self) -> TalkingShotInput:
        if (self.image_asset_id is None) == (self.video_asset_id is None):
            raise ValueError("give a picture or a clip, not both")
        if bool(self.script.strip()) == (self.audio_asset_id is not None):
            raise ValueError("give a script or recorded speech, not both")
        return self

    @property
    def source(self) -> Literal["image", "video"]:
        return "image" if self.image_asset_id else "video"

    @property
    def source_id(self) -> str:
        return str(self.image_asset_id or self.video_asset_id)


def sync_key(i: int, model_id: str) -> str:
    return f"sync[{i}:{model_id}]"


class TalkingShotRecipe(Recipe):
    id = "talking_shot"
    name = "Talking shot"
    summary = "A picture or a clip speaks your script, on one model or several to compare."
    Input = TalkingShotInput

    def title(self, inp: TalkingShotInput) -> str:
        words = inp.script.strip() or "recorded speech"
        return f"Talking shot: {words[:70]}"

    def validate(self, inp: TalkingShotInput, reg: Registry) -> list[str]:
        errors = []
        if inp.script.strip():
            voice = reg.model(inp.voice_model)
            if voice is None or mode_with_op(voice, "tts") is None:
                errors.append(f"{inp.voice_model!r} can't speak a script")
        for m in inp.models:
            spec = reg.model(m.model_id)
            if spec is None or lipsync_mode(spec, inp.source) is None:
                what = "a picture" if inp.source == "image" else "a clip"
                errors.append(f"{spec.name if spec else repr(m.model_id)} can't make {what} speak")
        if len({m.model_id for m in inp.models}) != len(inp.models):
            errors.append("pick each model once")
        return errors

    def _sync_step(self, inp: TalkingShotInput, spec: ModelSpec, mode: Mode, params: dict[str, Any],
                   audio: str) -> dict[str, Any]:
        prompt = inp.prompt.strip() or (DEFAULT_PROMPT if mode.prompt == "required" else "")
        step: dict[str, Any] = {
            "model_id": spec.id, "mode": mode.id, "prompt": prompt if mode.prompt != "none" else "",
            "params": params,
            "media": [{"role": SOURCE_ROLE[inp.source], "asset_id": inp.source_id},
                      {"role": "input_audio", "asset_id": audio}],
        }
        if inp.project_id:
            step["project_id"] = str(inp.project_id)
            if inp.shot_key:
                step["shot_key"] = inp.shot_key
        return step

    def jobs(self, inp: TalkingShotInput, reg: Registry) -> list[PlannedJob]:
        out: list[PlannedJob] = []
        planned: tuple[tuple[str, float], ...] = ()
        known: tuple[tuple[str, str], ...] = ((SOURCE_ROLE[inp.source], inp.source_id),)
        if inp.script.strip():
            voice = reg.models[inp.voice_model]
            tts = mode_with_op(voice, "tts")
            assert tts is not None
            out.append(PlannedJob(voice.id, tts.id, dict(inp.voice), prompt_chars=len(inp.script.strip())))
            planned = (("input_audio", spoken_seconds_estimate(inp.script)),)
        else:
            known = (*known, ("input_audio", str(inp.audio_asset_id)))
        for m in inp.models:
            spec = reg.models[m.model_id]
            mode = lipsync_mode(spec, inp.source)
            assert mode is not None
            out.append(PlannedJob(spec.id, mode.id, dict(m.params), (SOURCE_ROLE[inp.source], "input_audio"),
                                  media=known, planned_seconds=planned))
        return out

    def plan(self, inp: TalkingShotInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs: list[StepSpec] = []
        deps: tuple[str, ...] = ()
        audio = str(inp.audio_asset_id) if inp.audio_asset_id else None
        if inp.script.strip():
            voice = reg.models[inp.voice_model]
            tts = mode_with_op(voice, "tts")
            assert tts is not None
            step = {"model_id": voice.id, "mode": tts.id, "prompt": inp.script.strip(), "params": dict(inp.voice)}
            if inp.project_id:
                step["project_id"] = str(inp.project_id)
            specs.append(StepSpec("voice", StepKind.generate, "Voice the script", step))
            spoken = ok(settled, "voice")
            if spoken is None:
                return specs
            audio, deps = spoken["asset_id"], ("voice",)
        assert audio is not None

        keys = []
        for i, m in enumerate(inp.models, 1):
            spec = reg.models[m.model_id]
            mode = lipsync_mode(spec, inp.source)
            assert mode is not None
            key = sync_key(i, spec.id)
            keys.append(key)
            specs.append(StepSpec(key, StepKind.generate, f"{spec.name}: lip sync",
                                  self._sync_step(inp, spec, mode, dict(m.params), audio), deps,
                                  optional=len(inp.models) > 1))  # one model failing never sinks a comparison
        good = [ok(settled, k) for k in keys if ok(settled, k)]
        if len(good) > 1 and all(k in settled for k in keys):
            specs.append(StepSpec("pick", StepKind.checkpoint, "Pick the best take", {
                "type": "pick_one",
                "options": [{"value": settled[k]["asset_id"], "label": reg.models[m.model_id].name}
                            for k, m in zip(keys, inp.models, strict=True) if ok(settled, k)],
            }, tuple(keys)))
        return specs

    def _takes(self, inp: TalkingShotInput, settled: Settled) -> list[dict[str, Any]] | None:
        keys = [sync_key(i, m.model_id) for i, m in enumerate(inp.models, 1)]
        if not all(k in settled for k in keys):
            return None
        return [{"model_id": m.model_id, "asset_id": (ok(settled, k) or {}).get("asset_id"),
                 "error": settled[k].get("failed")} for k, m in zip(keys, inp.models, strict=True)]

    def result(self, inp: TalkingShotInput, settled: Settled) -> dict[str, Any] | None:
        takes = self._takes(inp, settled)
        if takes is None:
            return None
        good = [t for t in takes if t["asset_id"]]
        voice = ok(settled, "voice")
        audio = voice["asset_id"] if voice else (str(inp.audio_asset_id) if inp.audio_asset_id else None)
        if len(good) > 1:
            pick = ok(settled, "pick")
            if pick is None:
                return None
            return {"asset_id": pick["choice"], "takes": takes, "audio_asset_id": audio}
        return {"asset_id": good[0]["asset_id"] if good else None, "takes": takes, "audio_asset_id": audio}

    def failure(self, inp: TalkingShotInput, settled: Settled) -> str | None:
        takes = self._takes(inp, settled)
        if takes is not None and not any(t["asset_id"] for t in takes):
            return "No model made the shot speak. Check each take's error and try again."
        return None
