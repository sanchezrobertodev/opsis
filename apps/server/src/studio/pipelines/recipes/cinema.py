"""Cinema Studio runs. The project document is never written here: runs make hero frames and takes (generations
tagged with the project and shot) and the editor picks them into the document.

- cinema_frames: a grid of opening frames per shot, on an image model (an @mentioned character's pictures attach
  when the model takes references).
- cinema_takes: several takes per shot. A shot starts from its picked hero frame, or continues from the last frame
  of the previous shot (its picked take, or the first take this run makes of it). Editing a shot and revising the
  run redoes that shot and the shots chained after it, nothing else.
- cinema_export: the cut. Picked takes in order, trimmed, joined with cuts or short fades.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from studio.db.models import StepKind
from studio.director.elements import mentions
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_for, start_frame_mode, text_mode, with_param
from studio.pipelines.recipes.keyframe import ref_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

KEY = r"^[a-z][a-z0-9]{2,15}$"


def frames_key(shot: str, n: int | None = None) -> str:
    return f"frames[{shot}]" if n is None else f"frames[{shot}:{n}]"


def take_key(shot: str, n: int) -> str:
    return f"take[{shot}:{n}]"


def chain_key(shot: str) -> str:
    return f"chain[{shot}]"


# ---------------------------------------------------------------- hero frames


class FrameShot(BaseModel):
    key: str = Field(pattern=KEY)
    prompt: str = Field(min_length=1, max_length=2000)
    direction: dict[str, Any] | None = None


class CinemaFramesInput(BaseModel):
    project_id: uuid.UUID
    shots: list[FrameShot] = Field(min_length=1, max_length=40)
    image_model_id: str = "nano-banana-2"
    aspect_ratio: str = "16:9"
    count: int = Field(4, ge=1, le=4)


def frame_prompt(action: str) -> str:
    return f"A film still, the opening frame of the shot: {action.strip().rstrip('.')}. Cinematic composition."


class CinemaFramesRecipe(Recipe):
    id = "cinema_frames"
    name = "Hero frames"
    summary = "Opening frames for each shot, to pick from before filming."
    Input = CinemaFramesInput

    def title(self, inp: CinemaFramesInput) -> str:
        return f"Hero frames: {len(inp.shots)} shot{'s' if len(inp.shots) > 1 else ''}"

    def validate(self, inp: CinemaFramesInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.image_model_id)
        if spec is None or spec.output_type != "image" or text_mode(spec) is None:
            return [f"{inp.image_model_id!r} can't make images from text"]
        return []

    def _mode(self, spec: ModelSpec, prompt: str) -> Mode:
        """References for @mentioned characters when the model takes them; plain text to image otherwise."""
        if mentions(prompt) and (spec.elements or {}).get("strategy") == "refs_native" and (m := ref_mode(spec)):
            return m
        m = text_mode(spec)
        assert m is not None
        return m

    def _batched(self, spec: ModelSpec, mode: Mode) -> bool:
        return any(p.name == "num_images" for p in spec.params_for(mode.id))

    def _params(self, inp: CinemaFramesInput, spec: ModelSpec, mode: Mode) -> dict[str, Any]:
        params = with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)
        return with_param(spec, mode.id, params, "num_images", inp.count) if self._batched(spec, mode) else params

    def jobs(self, inp: CinemaFramesInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.image_model_id]
        role = (spec.elements or {}).get("ref_role", "image_references")
        out = []
        for shot in inp.shots:
            mode = self._mode(spec, shot.prompt)
            n = 1 if self._batched(spec, mode) else inp.count
            # the @mentioned characters' pictures fill the reference slot when the frame is made
            roles = (role,) if mode.slot(role) else ()
            out += [PlannedJob(spec.id, mode.id, self._params(inp, spec, mode), roles)] * n
        return out

    def plan(self, inp: CinemaFramesInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.image_model_id]
        specs = []
        for shot in inp.shots:
            mode = self._mode(spec, shot.prompt)
            step = {"model_id": spec.id, "mode": mode.id, "prompt": frame_prompt(shot.prompt), "media": [],
                    "params": self._params(inp, spec, mode), "direction": shot.direction,
                    "project_id": str(inp.project_id), "shot_key": shot.key}
            keys = [frames_key(shot.key)] if self._batched(spec, mode) else \
                [frames_key(shot.key, n) for n in range(1, inp.count + 1)]
            specs += [StepSpec(k, StepKind.generate, f"Hero frames: {shot.prompt[:60]}", step, optional=True)
                      for k in keys]
        return specs

    def result(self, inp: CinemaFramesInput, settled: Settled) -> dict[str, Any] | None:
        out: dict[str, list[str]] = {}
        for shot in inp.shots:
            keys = [k for k in settled if k == frames_key(shot.key) or k.startswith(f"frames[{shot.key}:")]
            expected = 1 if frames_key(shot.key) in settled else inp.count
            if len(keys) < expected:
                return None
            out[shot.key] = [a for k in keys for a in (settled[k].get("asset_ids") or [])]
        return {"frames": out}


# ---------------------------------------------------------------- takes


class TakeShot(BaseModel):
    key: str = Field(pattern=KEY)
    prompt: str = Field(min_length=1, max_length=2000)
    direction: dict[str, Any] | None = None
    duration: int | None = Field(None, ge=1, le=30)
    start_asset_id: uuid.UUID | None = Field(None, description="The picked hero frame")
    continue_from: str | None = Field(None, pattern=KEY, description="An earlier shot in this run to continue from")
    continue_from_asset_id: uuid.UUID | None = Field(None, description="A picked take to continue from")


class CinemaTakesInput(BaseModel):
    project_id: uuid.UUID
    model_id: str
    params: dict[str, Any] = {}
    takes: int = Field(2, ge=1, le=4)
    shots: list[TakeShot] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _chains_point_back(self) -> CinemaTakesInput:
        seen: set[str] = set()
        for shot in self.shots:
            if shot.continue_from and shot.continue_from not in seen:
                raise ValueError(f"shot {shot.key} continues from {shot.continue_from}, which must come before it")
            seen.add(shot.key)
        return self


class CinemaTakesRecipe(Recipe):
    id = "cinema_takes"
    name = "Film the shots"
    summary = "Several takes of each shot, chained where a shot continues the one before."
    Input = CinemaTakesInput

    def title(self, inp: CinemaTakesInput) -> str:
        return f"Takes: {len(inp.shots)} shot{'s' if len(inp.shots) > 1 else ''} x {inp.takes}"

    def validate(self, inp: CinemaTakesInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        if spec is None or spec.output_type != "video":
            return [f"{inp.model_id!r} is not a video model"]
        needs_frame = any(s.start_asset_id or s.continue_from or s.continue_from_asset_id for s in inp.shots)
        if needs_frame and start_frame_mode(spec) is None:
            return [f"{spec.name} can't start from an image, so hero frames and continuity can't be used"]
        if not all(s.start_asset_id or s.continue_from or s.continue_from_asset_id for s in inp.shots) \
                and text_mode(spec) is None:
            return [f"{spec.name} needs a hero frame for every shot"]
        return []

    def _params(self, inp: CinemaTakesInput, spec: ModelSpec, mode: Mode, shot: TakeShot) -> dict[str, Any]:
        return with_param(spec, mode.id, dict(inp.params), "duration", shot.duration)

    def jobs(self, inp: CinemaTakesInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        out = []
        for shot in inp.shots:
            roles = {"start_image"} if shot.start_asset_id or shot.continue_from or shot.continue_from_asset_id else set()
            mode = mode_for(spec, roles)
            assert mode is not None
            out += [PlannedJob(spec.id, mode.id, self._params(inp, spec, mode, shot), tuple(roles))] * inp.takes
        return out

    def plan(self, inp: CinemaTakesInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        specs: list[StepSpec] = []
        for shot in inp.shots:
            start: str | None = str(shot.start_asset_id) if shot.start_asset_id else None
            deps: tuple[str, ...] = ()
            source = str(shot.continue_from_asset_id) if shot.continue_from_asset_id else None
            if shot.continue_from and not source:
                # wait for every take of the shot it continues, then use the first good one (by take number), so
                # the choice never changes as late takes come in
                keys = tuple(take_key(shot.continue_from, n) for n in range(1, inp.takes + 1))
                if not all(k in settled for k in keys):
                    continue
                first = next((ok(settled, k) for k in keys if ok(settled, k)), None)
                if first is None:
                    continue  # nothing to continue from; reported in the result
                source, deps = first["asset_id"], keys
            if source:
                specs.append(StepSpec(chain_key(shot.key), StepKind.local, f"Last frame before {shot.key}",
                                      {"op": "last_frame", "asset_id": source}, deps))
                frame = ok(settled, chain_key(shot.key))
                if frame is None:
                    continue
                start, deps = frame["asset_id"], (chain_key(shot.key),)
            media = [{"role": "start_image", "asset_id": start}] if start else []
            mode = mode_for(spec, {m["role"] for m in media})
            assert mode is not None
            for n in range(1, inp.takes + 1):
                specs.append(StepSpec(take_key(shot.key, n), StepKind.generate, f"{shot.key} take {n}", {
                    "model_id": spec.id, "mode": mode.id, "prompt": shot.prompt, "media": media,
                    "params": self._params(inp, spec, mode, shot), "direction": shot.direction,
                    "project_id": str(inp.project_id), "shot_key": shot.key, "take": n,
                }, deps, optional=True))
        return specs

    def result(self, inp: CinemaTakesInput, settled: Settled) -> dict[str, Any] | None:
        out: dict[str, list[str]] = {}
        for shot in inp.shots:
            keys = [take_key(shot.key, n) for n in range(1, inp.takes + 1)]
            if not all(k in settled for k in keys):
                # a chained shot whose source never produced a take can't be filmed; report it as missing
                if shot.continue_from and all(take_key(shot.continue_from, n) in settled and
                                              not ok(settled, take_key(shot.continue_from, n))
                                              for n in range(1, inp.takes + 1)):
                    out[shot.key] = []
                    continue
                return None
            out[shot.key] = [settled[k]["asset_id"] for k in keys if ok(settled, k)]
        return {"takes": out}

    def failure(self, inp: CinemaTakesInput, settled: Settled) -> str | None:
        result = self.result(inp, settled)
        if result is not None and not any(result["takes"].values()):
            return "No take came out. Check each take's error and try again."
        return None


# ---------------------------------------------------------------- export


class CutClip(BaseModel):
    asset_id: uuid.UUID
    trim_in: float = Field(0.0, ge=0)
    trim_out: float | None = Field(None, gt=0)
    transition: Literal["cut", "fade"] = "cut"


class CinemaExportInput(BaseModel):
    project_id: uuid.UUID
    title: str = Field("Cut", max_length=200)
    clips: list[CutClip] = Field(min_length=1, max_length=120)
    fade_seconds: float = Field(0.5, ge=0.1, le=2.0)


class CinemaExportRecipe(Recipe):
    id = "cinema_export"
    name = "Export the cut"
    summary = "The picked takes in order, trimmed and joined into one film."
    Input = CinemaExportInput

    def title(self, inp: CinemaExportInput) -> str:
        return f"Cut: {inp.title}"

    def jobs(self, inp: CinemaExportInput, reg: Registry) -> list[PlannedJob]:
        return []

    def plan(self, inp: CinemaExportInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        return [StepSpec("cut", StepKind.local, "Cut the film", {
            "op": "timeline_cut", "title": inp.title, "fade_seconds": inp.fade_seconds,
            "clips": [c.model_dump(mode="json") for c in inp.clips]})]

    def result(self, inp: CinemaExportInput, settled: Settled) -> dict[str, Any] | None:
        cut = ok(settled, "cut")
        return {"asset_id": cut["asset_id"], "duration_ms": cut.get("duration_ms")} if cut else None
