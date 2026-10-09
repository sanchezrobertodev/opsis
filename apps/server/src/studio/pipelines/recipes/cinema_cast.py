"""Reference pictures for a film's new cast: one front portrait per character, drawn from the look it was saved
with and kept on its Element. Hero frames on models that take references then attach that picture wherever the
character is @mentioned, so the same face appears from shot to shot (and from there in every take)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import text_mode, with_param
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

PORTRAIT = ("Character reference portrait of @{slug}: front view, head and shoulders, neutral expression, looking at "
            "the camera, plain light grey background, soft even light, sharp focus on the face.")


class CastRef(BaseModel):
    element_id: uuid.UUID
    slug: str = Field(min_length=1, max_length=40)


class CinemaCastInput(BaseModel):
    project_id: uuid.UUID | None = None
    characters: list[CastRef] = Field(min_length=1, max_length=12)
    image_model_id: str = "nano-banana-2"
    aspect_ratio: str = "3:4"


class CinemaCastRecipe(Recipe):
    id = "cinema_cast"
    name = "Cast pictures"
    summary = "A front portrait of each new character, kept as its reference picture."
    Input = CinemaCastInput

    def title(self, inp: CinemaCastInput) -> str:
        return "Pictures of " + ", ".join(f"@{c.slug}" for c in inp.characters)

    def validate(self, inp: CinemaCastInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.image_model_id)
        if spec is None or spec.output_type != "image" or text_mode(spec) is None:
            return [f"{inp.image_model_id!r} can't make images from text"]
        return []

    def _mode(self, reg: Registry, inp: CinemaCastInput) -> tuple[ModelSpec, Mode]:
        spec = reg.models[inp.image_model_id]
        return spec, text_mode(spec)  # type: ignore[return-value]  # validate() checked it has one

    def jobs(self, inp: CinemaCastInput, reg: Registry) -> list[PlannedJob]:
        spec, mode = self._mode(reg, inp)
        params = with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)
        return [PlannedJob(spec.id, mode.id, params, key=c.slug) for c in inp.characters]

    def plan(self, inp: CinemaCastInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec, mode = self._mode(reg, inp)
        params = with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)
        specs = []
        for c in inp.characters:
            key = f"portrait[{c.slug}]"
            specs.append(StepSpec(key, StepKind.generate, f"A portrait of @{c.slug}", {
                "model_id": spec.id, "mode": mode.id, "prompt": PORTRAIT.format(slug=c.slug), "params": params,
                "media": [], "direction": None}))  # not tagged with the film: a portrait isn't one of its takes
            if out := ok(settled, key):
                specs.append(StepSpec(f"keep[{c.slug}]", StepKind.local, f"Keep it as @{c.slug}'s picture", {
                    "op": "add_element_image", "element_id": str(c.element_id), "asset_id": out["asset_id"],
                    "view": "front"}, (key,)))
        return specs

    def result(self, inp: CinemaCastInput, settled: Settled) -> dict[str, Any] | None:
        kept = {c.slug: ok(settled, f"keep[{c.slug}]") for c in inp.characters}
        if not all(kept.values()):
            return None
        return {"pictures": {slug: out["asset_id"] for slug, out in kept.items() if out}}
