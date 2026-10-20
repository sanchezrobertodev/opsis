"""Dub: a clip or a recording translated into another language, in the speakers' own voices.

  (a clip) audio (its sound) -> change (a dubbing model, `dub` op) -> (a clip) mux (the dub under the picture),
  or, with `lip_sync`, mux is a lip-sync model re-timing the mouths to the dub (a clip-driven `lipsync` mode)

The steps are the revoice recipe's; only the model, its params and the optional lip sync differ.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, StepSpec
from studio.pipelines.recipes.revoice import RevoiceRecipe
from studio.pipelines.recipes.talking import lipsync_mode
from studio.registry.loader import Registry


class DubInput(BaseModel):
    video_asset_id: uuid.UUID | None = Field(None, description="A clip whose speech is dubbed")
    audio_asset_id: uuid.UUID | None = Field(None, description="Or a recording")
    target_language: str = Field(min_length=2, max_length=12, description="The language to dub into (a BCP-47 tag)")
    source_language: str = Field("auto", max_length=12, description="The language spoken, or auto to detect it")
    quality: str = Field("dubbing_v2", description="The dubbing model's version")
    lip_sync: bool = Field(False, description="Re-time the mouths in the clip to the dub (a clip only)")
    dub_model: str = "eleven-dubbing"
    lipsync_model: str = "sync-lipsync-3"
    clip_seconds: float | None = Field(None, description="Filled in when the run starts: the clip's length")

    @model_validator(mode="after")
    def _one_source(self) -> DubInput:
        if (self.video_asset_id is None) == (self.audio_asset_id is None):
            raise ValueError("give a clip or a recording, not both")
        if self.lip_sync and self.video_asset_id is None:
            raise ValueError("lip sync needs a clip")
        return self


class DubRecipe(RevoiceRecipe):
    id = "dub"
    name = "Dub"
    summary = "A clip or a recording translated into another language in the speakers' own voices."
    Input = DubInput
    op, change_label, mux_label = "dub", "Dub the speech", "Put the dub on the clip"

    def _model(self, inp: DubInput) -> str:
        return inp.dub_model

    def _params(self, inp: DubInput) -> dict[str, Any]:
        return {"target_language": inp.target_language, "source_language": inp.source_language,
                "quality": inp.quality}

    def validate(self, inp: DubInput, reg: Registry) -> list[str]:
        problems = super().validate(inp, reg)
        if inp.lip_sync:
            lips = reg.model(inp.lipsync_model)
            if lips is None or lipsync_mode(lips, "video") is None:
                problems.append(f"{inp.lipsync_model!r} can't lip-sync a clip")
        return problems

    def jobs(self, inp: DubInput, reg: Registry) -> list[PlannedJob]:
        jobs = super().jobs(inp, reg)
        if inp.lip_sync:
            lips = reg.models[inp.lipsync_model]
            seconds = inp.clip_seconds or 0.0  # the dub is about as long as the clip
            jobs.append(PlannedJob(lips.id, lipsync_mode(lips, "video").id, {},  # type: ignore[union-attr]
                                   ("input_video", "input_audio"), media=(("input_video", str(inp.video_asset_id)),),
                                   planned_seconds=(("input_audio", seconds),)))
        return jobs

    def _finish(self, inp: DubInput, audio: str, reg: Registry) -> StepSpec:
        if not inp.lip_sync:
            return super()._finish(inp, audio, reg)
        lips = reg.models[inp.lipsync_model]
        return StepSpec("mux", StepKind.generate, "Lip-sync the clip to the dub", {
            "model_id": lips.id, "mode": lipsync_mode(lips, "video").id,  # type: ignore[union-attr]
            "prompt": "", "params": {},
            "media": [{"role": "input_video", "asset_id": str(inp.video_asset_id)},
                      {"role": "input_audio", "asset_id": audio}]}, ("change",))
