"""Re-voice: a recording, or a clip's speech, said again in another voice with the same words, timing and emotion.

  (a clip) audio (its sound) -> change (a voice changer, `voice_change` op) -> (a clip) mux (the new sound under it)

The voice can be a stock one or one you cloned; the voice changer's `voice_id` takes either.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Asset, StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_with_op
from studio.registry.loader import Registry


class RevoiceInput(BaseModel):
    video_asset_id: uuid.UUID | None = Field(None, description="A clip whose speech is re-voiced")
    audio_asset_id: uuid.UUID | None = Field(None, description="Or a recording")
    voice_id: str = Field(min_length=1, max_length=80, description="The voice to say it in (stock or cloned)")
    remove_background_noise: bool = False
    voice_model: str = "eleven-voice-changer"
    clip_seconds: float | None = Field(None, description="Filled in when the run starts: the clip's length")

    @model_validator(mode="after")
    def _one_source(self) -> RevoiceInput:
        if (self.video_asset_id is None) == (self.audio_asset_id is None):
            raise ValueError("give a clip or a recording, not both")
        return self


class RevoiceRecipe(Recipe):
    id = "revoice"
    name = "Change the voice"
    summary = "A clip or a recording said again in another voice, keeping its words, timing and emotion."
    Input = RevoiceInput

    def validate(self, inp: RevoiceInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.voice_model)
        if spec is None:
            return [f"unknown model {inp.voice_model!r}"]
        return [] if mode_with_op(spec, "voice_change") else [f"{spec.name} can't change a voice"]

    def _params(self, inp: RevoiceInput) -> dict[str, Any]:
        return {"voice_id": inp.voice_id, "remove_background_noise": inp.remove_background_noise}

    async def resolve(self, session: AsyncSession, user_id: uuid.UUID, inp: RevoiceInput,
                      reg: Registry) -> RevoiceInput:
        """A clip's sound is as long as the clip: measured here, so the estimate prices it."""
        if inp.video_asset_id is None:
            return inp
        ms = (await session.execute(select(Asset.duration_ms).where(
            Asset.id == inp.video_asset_id, Asset.user_id == user_id))).scalar_one_or_none()
        return inp.model_copy(update={"clip_seconds": (ms or 0) / 1000})

    def jobs(self, inp: RevoiceInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.voice_model]
        sound = ({"media": (("input_audio", str(inp.audio_asset_id)),)} if inp.audio_asset_id
                 else {"planned_seconds": (("input_audio", inp.clip_seconds or 0.0),)})
        return [PlannedJob(spec.id, mode_with_op(spec, "voice_change").id, self._params(inp),  # type: ignore[union-attr]
                           ("input_audio",), **sound)]

    def plan(self, inp: RevoiceInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs: list[StepSpec] = []
        audio, after = (str(inp.audio_asset_id) if inp.audio_asset_id else None), ()
        if inp.video_asset_id:
            specs.append(StepSpec("audio", StepKind.local, "Take the clip's sound", {
                "op": "extract_audio", "video": str(inp.video_asset_id)}))
            sound = ok(settled, "audio")
            if sound is None:
                return specs
            audio, after = sound["asset_id"], ("audio",)
        spec = reg.models[inp.voice_model]
        specs.append(StepSpec("change", StepKind.generate, "Change the voice", {
            "model_id": spec.id, "mode": mode_with_op(spec, "voice_change").id,  # type: ignore[union-attr]
            "prompt": "", "params": self._params(inp), "media": [{"role": "input_audio", "asset_id": audio}]}, after))
        changed = ok(settled, "change")
        if inp.video_asset_id and changed:
            specs.append(StepSpec("mux", StepKind.local, "Put the new voice on the clip", {
                "op": "replace_audio", "video": str(inp.video_asset_id), "audio": changed["asset_id"]}, ("change",)))
        return specs

    def result(self, inp: RevoiceInput, settled: Settled) -> dict[str, Any] | None:
        done = ok(settled, "mux" if inp.video_asset_id else "change")
        return {"asset_id": done["asset_id"]} if done else None
