"""Re-voice: a recording, or a clip's speech, said again in another voice with the same words, timing and emotion.

  (a clip) audio (its sound) -> change (a voice changer, `voice_change` op) -> (a clip) mux (the new sound under it)

The voice can be a stock one or one you cloned; the voice changer's `voice_id` takes either. `dub.py` reuses these
steps with a dubbing model in place of the voice changer.
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
    op, change_label, mux_label = "voice_change", "Change the voice", "Put the new voice on the clip"

    def _model(self, inp: Any) -> str:
        return inp.voice_model

    def validate(self, inp: Any, reg: Registry) -> list[str]:
        spec = reg.model(self._model(inp))
        if spec is None:
            return [f"unknown model {self._model(inp)!r}"]
        return [] if mode_with_op(spec, self.op) else [f"{spec.name} can't {self.change_label.lower()}"]

    def _params(self, inp: Any) -> dict[str, Any]:
        return {"voice_id": inp.voice_id, "remove_background_noise": inp.remove_background_noise}

    async def resolve(self, session: AsyncSession, user_id: uuid.UUID, inp: Any, reg: Registry) -> Any:
        """A clip's sound is as long as the clip: measured here, so the estimate prices it."""
        if inp.video_asset_id is None:
            return inp
        ms = (await session.execute(select(Asset.duration_ms).where(
            Asset.id == inp.video_asset_id, Asset.user_id == user_id))).scalar_one_or_none()
        return inp.model_copy(update={"clip_seconds": (ms or 0) / 1000})

    def jobs(self, inp: Any, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[self._model(inp)]
        sound = ({"media": (("input_audio", str(inp.audio_asset_id)),)} if inp.audio_asset_id
                 else {"planned_seconds": (("input_audio", inp.clip_seconds or 0.0),)})
        return [PlannedJob(spec.id, mode_with_op(spec, self.op).id, self._params(inp),  # type: ignore[union-attr]
                           ("input_audio",), **sound)]

    def plan(self, inp: Any, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs: list[StepSpec] = []
        audio, after = (str(inp.audio_asset_id) if inp.audio_asset_id else None), ()
        if inp.video_asset_id:
            specs.append(StepSpec("audio", StepKind.local, "Take the clip's sound", {
                "op": "extract_audio", "video": str(inp.video_asset_id)}))
            sound = ok(settled, "audio")
            if sound is None:
                return specs
            audio, after = sound["asset_id"], ("audio",)
        spec = reg.models[self._model(inp)]
        specs.append(StepSpec("change", StepKind.generate, self.change_label, {
            "model_id": spec.id, "mode": mode_with_op(spec, self.op).id,  # type: ignore[union-attr]
            "prompt": "", "params": self._params(inp), "media": [{"role": "input_audio", "asset_id": audio}]}, after))
        changed = ok(settled, "change")
        if inp.video_asset_id and changed:
            specs.append(self._finish(inp, changed["asset_id"], reg))
        return specs

    def _finish(self, inp: Any, audio: str, reg: Registry) -> StepSpec:
        """The clip with the new sound: keyed `mux` for `result`."""
        return StepSpec("mux", StepKind.local, self.mux_label, {
            "op": "replace_audio", "video": str(inp.video_asset_id), "audio": audio}, ("change",))

    def result(self, inp: Any, settled: Settled) -> dict[str, Any] | None:
        done = ok(settled, "mux" if inp.video_asset_id else "change")
        return {"asset_id": done["asset_id"]} if done else None
