"""3D Jutsu: a blocked-out 3D scene made into footage. The editor renders the passes in the browser; this run
takes those renders as inputs and never touches the scene document.

- angles: each still (the shot camera at its keyed moments) is redrawn as a finished frame on an image model,
  keeping the angle, framing and poses (the still is the first reference; @mentions and style pictures add
  more). One still is animated from its frame; several are animated pairwise, each clip from one frame to the
  next on a start+end-frame model, then cut together.
- depth: the depth pass drives a depth-to-video model (Wan VACE depth), so the motion and shapes follow the
  blocking exactly. An optional still is redrawn first and used as the opening frame.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    bridge_mode,
    fit_duration,
    mode_with_op,
    start_frame_mode,
    with_param,
)
from studio.pipelines.recipes.keyframe import ref_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

EDGE = 1 / 24  # the frame two clips share where one ends on the frame the next starts from
MAX_STILLS = 8


class Still(BaseModel):
    asset_id: uuid.UUID
    time: float = Field(0, ge=0, le=60, description="Seconds into the scene this angle is seen")


class Jutsu3dInput(BaseModel):
    project_id: uuid.UUID
    scene_version: int | None = None
    path: Literal["angles", "depth"] = "angles"
    prompt: str = Field(min_length=1, max_length=2000, description="What it should look like, with @mentions")
    direction: dict[str, Any] | None = None
    model_id: str = Field(description="Angles: a video model that animates frames. Depth: a depth-to-video model.")
    params: dict[str, Any] = {}
    stills: list[Still] = Field(default_factory=list, max_length=MAX_STILLS)
    depth_asset_id: uuid.UUID | None = None
    image_model_id: str = Field("nano-banana-2", description="Redraws the blocking stills as finished frames")
    reference_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _inputs_for_the_path(self) -> Jutsu3dInput:
        if self.path == "angles" and not self.stills:
            raise ValueError("the angles path needs at least one rendered still")
        if self.path == "depth" and self.depth_asset_id is None:
            raise ValueError("the depth path needs the rendered depth video")
        if self.path == "depth" and len(self.stills) > 1:
            raise ValueError("the depth path takes at most one still (the opening frame)")
        return self


def look_prompt(prompt: str) -> str:
    return (f"Render this 3D blocking frame as a finished film still: {prompt.strip().rstrip('.')}. Keep the exact "
            "camera angle, framing and composition, and every figure's position and pose; replace the grey "
            "stand-in shapes with real people, places and materials.")


def look_key(i: int) -> str:
    return f"look[{i:02d}]"


def clip_key(i: int) -> str:
    return f"clip[{i:02d}]"


def depth_mode(spec: ModelSpec) -> Mode | None:
    mode = mode_with_op(spec, "depth_to_video")
    return mode if mode is not None and mode.slot("control_video") else None


class Jutsu3dRecipe(Recipe):
    id = "jutsu3d"
    name = "3D scene to video"
    summary = "Make a blocked-out 3D scene real: redraw its angles and animate between them, or drive a depth model."
    Input = Jutsu3dInput

    def title(self, inp: Jutsu3dInput) -> str:
        return f"3D scene ({inp.path}): {inp.prompt[:60]}"

    # ------------------------------------------------------------ choosing modes

    def _image(self, inp: Jutsu3dInput, reg: Registry) -> tuple[ModelSpec, Mode, str]:
        image = reg.models[inp.image_model_id]
        mode = ref_mode(image)
        assert mode is not None
        return image, mode, (image.elements or {}).get("ref_role", "image_references")

    def _video_mode(self, inp: Jutsu3dInput, spec: ModelSpec) -> Mode | None:
        if inp.path == "depth":
            return depth_mode(spec)
        return start_frame_mode(spec) if len(inp.stills) == 1 else bridge_mode(spec)

    def validate(self, inp: Jutsu3dInput, reg: Registry) -> list[str]:
        errors: list[str] = []
        spec = reg.model(inp.model_id)
        if spec is None or spec.output_type != "video":
            errors.append(f"{inp.model_id!r} is not a video model")
        elif self._video_mode(inp, spec) is None:
            errors.append({
                "depth": f"{spec.name} can't follow a depth video; pick a depth-to-video model",
                "angles": f"{spec.name} can't start from a frame" if len(inp.stills) == 1 else
                f"{spec.name} can't end on a chosen frame, so it can't move between angles; "
                "pick a model with start and end frames",
            }[inp.path])
        if inp.stills:
            image = reg.model(inp.image_model_id)
            if image is None or image.output_type != "image" or ref_mode(image) is None:
                errors.append(f"{inp.image_model_id!r} can't redraw a picture from a reference")
        if inp.path == "angles":
            times = [s.time for s in inp.stills]
            if times != sorted(times):
                errors.append("the stills must be in time order")
        return errors

    def _clip_params(self, inp: Jutsu3dInput, spec: ModelSpec, mode: Mode, seconds: float | None) -> dict[str, Any]:
        params = dict(inp.params)
        if seconds is not None and "duration" not in params:
            params = with_param(spec, mode.id, params, "duration", fit_duration(spec, mode.id, seconds))
        return params

    def _gaps(self, inp: Jutsu3dInput) -> list[float]:
        """Seconds between consecutive angles (at least one, so a near-duplicate key still makes a clip)."""
        return [max(1.0, b.time - a.time) for a, b in zip(inp.stills, inp.stills[1:], strict=False)]

    def _look_params(self, inp: Jutsu3dInput, image: ModelSpec, mode: Mode) -> dict[str, Any]:
        return with_param(image, mode.id, {}, "aspect_ratio", inp.params.get("aspect_ratio"))

    # ------------------------------------------------------------ estimate

    def jobs(self, inp: Jutsu3dInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        mode = self._video_mode(inp, spec)
        assert mode is not None
        out: list[PlannedJob] = []
        if inp.stills:
            image, img_mode, role = self._image(inp, reg)
            out += [PlannedJob(image.id, img_mode.id, self._look_params(inp, image, img_mode), (role,))
                    for _ in inp.stills]
        if inp.path == "depth":
            roles = ("control_video", *(["start_image"] if inp.stills and mode.slot("start_image") else []))
            out.append(PlannedJob(spec.id, mode.id, dict(inp.params), roles,
                                  media=(("control_video", str(inp.depth_asset_id)),)))
        elif len(inp.stills) == 1:
            out.append(PlannedJob(spec.id, mode.id, self._clip_params(inp, spec, mode, None), ("start_image",)))
        else:
            out += [PlannedJob(spec.id, mode.id, self._clip_params(inp, spec, mode, gap), ("start_image", "end_image"))
                    for gap in self._gaps(inp)]
        return out

    # ------------------------------------------------------------ plan

    def _looks(self, inp: Jutsu3dInput, reg: Registry) -> list[StepSpec]:
        image, mode, role = self._image(inp, reg)
        slot = mode.slot(role)
        extra = [str(a) for a in inp.reference_asset_ids][: max(0, (slot.max if slot else 1) - 1)]
        return [StepSpec(look_key(i), StepKind.generate, f"Angle {i + 1} as a finished frame", {
            "model_id": image.id, "mode": mode.id, "prompt": look_prompt(inp.prompt),
            "media": [{"role": role, "asset_id": str(s.asset_id)}, *({"role": role, "asset_id": a} for a in extra)],
            "params": self._look_params(inp, image, mode), "project_id": str(inp.project_id)})
            for i, s in enumerate(inp.stills)]

    def plan(self, inp: Jutsu3dInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        mode = self._video_mode(inp, spec)
        assert mode is not None
        pid = str(inp.project_id)
        specs = self._looks(inp, reg) if inp.stills else []
        looks = [ok(settled, look_key(i)) for i in range(len(inp.stills))]
        frames = [(lk.get("asset_ids") or [lk["asset_id"]])[0] if lk else None for lk in looks]
        common = {"model_id": spec.id, "mode": mode.id, "prompt": inp.prompt, "direction": inp.direction,
                  "project_id": pid}

        if inp.path == "depth":
            if inp.stills and frames[0] is None:
                return specs
            media = [{"role": "control_video", "asset_id": str(inp.depth_asset_id)}]
            if frames and frames[0] and mode.slot("start_image"):
                media.append({"role": "start_image", "asset_id": frames[0]})
            refs = mode.slot("image_references")
            if refs and inp.reference_asset_ids:
                media += [{"role": "image_references", "asset_id": str(a)} for a in inp.reference_asset_ids[:refs.max]]
            specs.append(StepSpec(clip_key(0), StepKind.generate, f"Follow the depth pass on {spec.name}", {
                **common, "media": media, "params": dict(inp.params)},
                (look_key(0),) if inp.stills else ()))
            return specs

        if len(inp.stills) == 1:
            if frames[0] is not None:
                specs.append(StepSpec(clip_key(0), StepKind.generate, f"Animate on {spec.name}", {
                    **common, "media": [{"role": "start_image", "asset_id": frames[0]}],
                    "params": self._clip_params(inp, spec, mode, None)}, (look_key(0),)))
            return specs

        for i, gap in enumerate(self._gaps(inp)):
            start, end = frames[i], frames[i + 1]
            if start is None or end is None:
                continue
            specs.append(StepSpec(clip_key(i), StepKind.generate, f"Angle {i + 1} to {i + 2} on {spec.name}", {
                **common, "media": [{"role": "start_image", "asset_id": start}, {"role": "end_image", "asset_id": end}],
                "params": self._clip_params(inp, spec, mode, gap)}, (look_key(i), look_key(i + 1))))
        clips = [ok(settled, clip_key(i)) for i in range(len(inp.stills) - 1)]
        if all(clips):
            specs.append(StepSpec("cut", StepKind.local, "Cut the clips together", {
                "op": "timeline_cut", "title": f"3D scene: {inp.prompt[:60]}",
                "clips": [{"asset_id": c["asset_id"], **({"trim_in": EDGE} if i else {})}
                          for i, c in enumerate(clips) if c]},
                tuple(clip_key(i) for i in range(len(clips)))))
        return specs

    def result(self, inp: Jutsu3dInput, settled: Settled) -> dict[str, Any] | None:
        looks = [ok(settled, look_key(i)) for i in range(len(inp.stills))]
        final = ok(settled, "cut") if inp.path == "angles" and len(inp.stills) > 1 else ok(settled, clip_key(0))
        if final is None or not all(looks):
            return None
        return {"asset_id": final["asset_id"], "duration_ms": final.get("duration_ms"),
                "looks": [lk["asset_id"] for lk in looks if lk]}
