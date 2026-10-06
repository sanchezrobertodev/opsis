"""Shot sequence: several shots on one model, each starting from the last frame of the one before, then
joined into one cut. This is also how any model gets "extend": continue a clip from its final frame."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_for, start_frame_mode, text_mode, with_param
from studio.registry.loader import Registry


class Shot(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int | None = Field(None, ge=1, le=60, description="Seconds; the model's default when empty")


class SequenceInput(BaseModel):
    model_id: str
    shots: list[Shot] = Field(min_length=1, max_length=12)
    params: dict[str, Any] = {}
    direction: dict[str, Any] | None = Field(None, description="Camera moves and look applied to every shot")
    start_image_asset_id: uuid.UUID | None = Field(None, description="Optional first frame for shot 1")
    assemble: bool = Field(True, description="Join the shots into one video at the end")


def shot_key(i: int) -> str:
    return f"shot[{i:02d}]"


def frame_key(i: int) -> str:
    return f"frame[{i:02d}]"


class SequenceRecipe(Recipe):
    id = "sequence"
    name = "Shot sequence"
    summary = "Shots that continue from each other's last frame, joined into one cut."
    Input = SequenceInput

    def title(self, inp: SequenceInput) -> str:
        return f"Sequence: {inp.shots[0].prompt[:80]}"

    def validate(self, inp: SequenceInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        if spec is None:
            return [f"unknown model {inp.model_id!r}"]
        if spec.output_type != "video":
            return [f"{spec.name} makes {spec.output_type}s; a sequence needs a video model"]
        errors = []
        if len(inp.shots) > 1 and start_frame_mode(spec) is None:
            errors.append(f"{spec.name} can't start from an image, so shots can't continue from each other")
        if inp.start_image_asset_id is None and text_mode(spec) is None:
            errors.append(f"{spec.name} needs a start image for the first shot")
        return errors

    def _shot_params(self, inp: SequenceInput, reg: Registry, mode_id: str, shot: Shot) -> dict[str, Any]:
        spec = reg.models[inp.model_id]
        return with_param(spec, mode_id, dict(inp.params), "duration", shot.duration)

    def jobs(self, inp: SequenceInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        out = []
        for i, shot in enumerate(inp.shots, 1):
            roles = {"start_image"} if i > 1 or inp.start_image_asset_id else set()
            mode = mode_for(spec, roles)
            assert mode is not None
            out.append(PlannedJob(spec.id, mode.id, self._shot_params(inp, reg, mode.id, shot), tuple(roles)))
        return out

    def plan(self, inp: SequenceInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        specs: list[StepSpec] = []
        start = str(inp.start_image_asset_id) if inp.start_image_asset_id else None
        for i, shot in enumerate(inp.shots, 1):
            media = [{"role": "start_image", "asset_id": start}] if start else []
            mode = mode_for(spec, {m["role"] for m in media})
            assert mode is not None
            deps = (frame_key(i - 1),) if i > 1 else ()
            specs.append(StepSpec(shot_key(i), StepKind.generate, f"Shot {i}", {
                "model_id": spec.id, "mode": mode.id, "prompt": shot.prompt, "media": media,
                "params": self._shot_params(inp, reg, mode.id, shot), "direction": inp.direction,
            }, deps))
            if i == len(inp.shots):
                break
            done = ok(settled, shot_key(i))
            if done is None:
                break  # the next shot needs this one's last frame
            specs.append(StepSpec(frame_key(i), StepKind.local, f"Last frame of shot {i}",
                                  {"op": "last_frame", "asset_id": done["asset_id"]}, (shot_key(i),)))
            frame = ok(settled, frame_key(i))
            if frame is None:
                break
            start = frame["asset_id"]

        shots = [ok(settled, shot_key(i)) for i in range(1, len(inp.shots) + 1)]
        if inp.assemble and len(inp.shots) > 1 and all(shots):
            specs.append(StepSpec("cut", StepKind.local, "Join the shots", {
                "op": "concat", "asset_ids": [s["asset_id"] for s in shots if s], "title": self.title(inp),
            }, tuple(shot_key(i) for i in range(1, len(inp.shots) + 1))))
        return specs

    def result(self, inp: SequenceInput, settled: Settled) -> dict[str, Any] | None:
        shots = [ok(settled, shot_key(i)) for i in range(1, len(inp.shots) + 1)]
        if not all(shots):
            return None
        shot_ids = [s["asset_id"] for s in shots if s]
        if inp.assemble and len(shot_ids) > 1:
            cut = ok(settled, "cut")
            if cut is None:
                return None
            return {"asset_id": cut["asset_id"], "shot_asset_ids": shot_ids}
        return {"asset_id": shot_ids[-1], "shot_asset_ids": shot_ids}
