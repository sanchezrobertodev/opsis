"""Character shot (keyframe first): for video models that can't take reference images, render the opening
frame on an image model that can (the @mentioned characters' pictures attach there), then animate it."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.director.elements import mentions
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import start_frame_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec


class KeyframeInput(BaseModel):
    model_id: str = Field(description="The video model that animates the frame")
    prompt: str = Field(min_length=1, max_length=4000, description="The shot, with @mentions of saved characters")
    params: dict[str, Any] = {}
    direction: dict[str, Any] | None = None
    image_model_id: str = Field("nano-banana-2", description="Renders the opening frame from the references")


def ref_image(spec: ModelSpec) -> tuple[Mode, str]:
    """The model's mode that draws from reference pictures, and the role they go in."""
    mode = ref_mode(spec)
    assert mode is not None
    return mode, (spec.elements or {}).get("ref_role", "image_references")


def ref_mode(spec: ModelSpec) -> Mode | None:
    """The image model's mode that accepts reference images."""
    role = (spec.elements or {}).get("ref_role", "image_references")
    return next((m for m in spec.modes if m.slot(role)), None)


class KeyframeRecipe(Recipe):
    id = "keyframe"
    name = "Character shot"
    summary = "Render the opening frame from your characters' references, then animate it."
    Input = KeyframeInput

    def title(self, inp: KeyframeInput) -> str:
        return f"Character shot: {inp.prompt[:80]}"

    def validate(self, inp: KeyframeInput, reg: Registry) -> list[str]:
        video, image = reg.model(inp.model_id), reg.model(inp.image_model_id)
        errors = []
        if video is None or video.output_type != "video":
            errors.append(f"{inp.model_id!r} is not a video model")
        elif start_frame_mode(video) is None:
            errors.append(f"{video.name} can't start from an image")
        if image is None or image.output_type != "image":
            errors.append(f"{inp.image_model_id!r} is not an image model")
        elif (image.elements or {}).get("strategy") != "refs_native" or ref_mode(image) is None:
            errors.append(f"{image.name} can't take reference images")
        if not mentions(inp.prompt):
            errors.append("mention a saved character with @name so its pictures can shape the frame")
        return errors

    def _aspect(self, inp: KeyframeInput, reg: Registry) -> str | None:
        ar = inp.params.get("aspect_ratio")
        image = reg.models[inp.image_model_id]
        p = image.param("aspect_ratio")
        return ar if p is not None and ar in getattr(p, "options", []) else None

    def jobs(self, inp: KeyframeInput, reg: Registry) -> list[PlannedJob]:
        image, video = reg.models[inp.image_model_id], reg.models[inp.model_id]
        im, vm = ref_mode(image), start_frame_mode(video)
        assert im and vm
        role = (image.elements or {}).get("ref_role", "image_references")
        aspect = self._aspect(inp, reg)
        return [PlannedJob(image.id, im.id, {"aspect_ratio": aspect} if aspect else {}, (role,)),
                PlannedJob(video.id, vm.id, inp.params, ("start_image",))]

    def plan(self, inp: KeyframeInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        image, video = reg.models[inp.image_model_id], reg.models[inp.model_id]
        im, vm = ref_mode(image), start_frame_mode(video)
        assert im and vm
        aspect = self._aspect(inp, reg)
        specs = [StepSpec("keyframe", StepKind.generate, f"Opening frame on {image.name}", {
            "model_id": image.id, "mode": im.id, "media": [],  # the @mentions attach the references
            "prompt": f"The opening frame of a film shot: {inp.prompt}. Cinematic still, clear subject, natural light.",
            "params": {"aspect_ratio": aspect} if aspect else {}, "direction": None,
        })]
        frame = ok(settled, "keyframe")
        if frame:
            specs.append(StepSpec("shot", StepKind.generate, f"Animate on {video.name}", {
                "model_id": video.id, "mode": vm.id, "prompt": inp.prompt, "params": inp.params,
                "media": [{"role": "start_image", "asset_id": frame["asset_id"]}], "direction": inp.direction,
            }, ("keyframe",)))
        return specs

    def result(self, inp: KeyframeInput, settled: Settled) -> dict[str, Any] | None:
        shot = ok(settled, "shot")
        frame = ok(settled, "keyframe")
        if not shot or not frame:
            return None
        return {"asset_id": shot["asset_id"], "keyframe_asset_id": frame["asset_id"]}
