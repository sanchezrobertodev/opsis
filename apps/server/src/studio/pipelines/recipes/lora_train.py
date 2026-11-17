"""Train a character's identity (Soul ID): zip the character's photos, train a LoRA on them with the trainer picked
(Wan 2.2's by default, or FLUX 3 Klein's), store it on the character's Element next to the ones it has for other LoRA
bases, and draw one portrait with it. From then on, @mentioning the character on a model with the `lora` element
strategy and the same base loads the LoRA and writes its trigger word into the prompt."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from studio.db.models import StepKind
from studio.director.elements import TRIGGER
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_with_op, text_mode, with_param
from studio.registry.loader import Registry
from studio.registry.schema import IntParam, Mode, ModelSpec

TRAINER = "wan-2.2-lora-trainer"  # the default, so runs stored before there was a choice train as they did
MIN_PHOTOS, MAX_PHOTOS = 10, 80


class LoraTrainInput(BaseModel):
    element_id: str = Field(description="The character whose identity is learned")
    slug: str = Field(min_length=1, max_length=40, description="The character's @mention name, for the preview")
    photos: list[str] = Field(description=f"{MIN_PHOTOS}-{MAX_PHOTOS} picture asset ids of the character")
    trainer_model_id: str = Field(TRAINER, description="The trainer (a model that does `lora_train`); its LoRA base "
                                                       "decides which models load the identity")
    trigger: str | None = Field(None, description="A made-up lowercase word the LoRA learns as this character; one is made from the name when left out")
    steps: int = Field(1000, ge=100, le=3000, description="Within the trainer's own range")
    face_crop: bool = False
    preview: bool = True

    @field_validator("photos")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _trigger(self) -> LoraTrainInput:
        if not self.trigger:
            self.trigger = default_trigger(self.slug, self.element_id)
        return self


def default_trigger(slug: str, element_id: str) -> str:
    """A word the base model doesn't already know: the character's name squeezed, plus a short tail from its id."""
    stem = re.sub(r"[^a-z]", "", slug.lower())[:12] or "chr"
    tail = re.sub(r"[^a-z0-9]", "", element_id.lower())[-4:]
    return f"{stem}zq{tail}"[:24]


def trainer(inp: LoraTrainInput, reg: Registry) -> tuple[ModelSpec, Mode] | None:
    spec = reg.model(inp.trainer_model_id)
    mode = mode_with_op(spec, "lora_train") if spec else None
    return (spec, mode) if spec and mode else None


def preview_model(base: str | None, reg: Registry) -> tuple[ModelSpec, Mode] | None:
    """The first still model (by id) that loads identities for `base` and draws from a prompt alone."""
    for spec in sorted(reg.models.values(), key=lambda m: m.id):
        mode = text_mode(spec)
        if base and spec.output_type == "image" and spec.lora_base() == base and mode:
            return spec, mode
    return None


def train_params(spec: ModelSpec, mode: Mode, inp: LoraTrainInput) -> dict[str, Any]:
    """What the trainer takes of the input: a param it doesn't have (FLUX has no face crop) is left out."""
    params: dict[str, Any] = {}
    for name, value in (("trigger_phrase", inp.trigger), ("steps", inp.steps), ("face_crop", inp.face_crop)):
        params = with_param(spec, mode.id, params, name, value)
    return params


def portrait_params(spec: ModelSpec, mode: Mode) -> dict[str, Any]:
    return with_param(spec, mode.id, {}, "aspect_ratio", "3:4")


class LoraTrainRecipe(Recipe):
    id = "lora_train"
    name = "Train a character"
    summary = "Learn a character's face from their photos, so the models that load it keep it in every picture and clip."
    Input = LoraTrainInput

    def title(self, inp: LoraTrainInput) -> str:
        return f"Train @{inp.slug}"

    def validate(self, inp: LoraTrainInput, reg: Registry) -> list[str]:
        errors = []
        if not MIN_PHOTOS <= len(inp.photos) <= MAX_PHOTOS:
            errors.append(f"pick {MIN_PHOTOS} to {MAX_PHOTOS} photos of the character ({len(inp.photos)} given)")
        if not re.match(TRIGGER, inp.trigger or ""):
            errors.append("the trigger word must be 4-24 lowercase letters and digits, starting with a letter")
        if (picked := trainer(inp, reg)) is None:
            return [*errors, f"{inp.trainer_model_id} isn't a LoRA trainer in the registry"]
        spec, _ = picked
        steps = spec.param("steps")
        if isinstance(steps, IntParam) and not steps.min <= inp.steps <= steps.max:
            errors.append(f"{spec.name} trains for {steps.min} to {steps.max} steps ({inp.steps} given)")
        if spec.lora_base() is None:
            errors.append(f"{spec.name} doesn't say which models load what it trains (its route's output.meta.base)")
        elif inp.preview and preview_model(spec.lora_base(), reg) is None:
            errors.append(f"no still model loads {spec.name}'s identities for the portrait; turn the preview off")
        return errors

    def jobs(self, inp: LoraTrainInput, reg: Registry) -> list[PlannedJob]:
        if (picked := trainer(inp, reg)) is None:
            return []
        spec, mode = picked
        jobs = [PlannedJob(spec.id, mode.id, train_params(spec, mode, inp), ("training_data",))]
        if inp.preview and (preview := preview_model(spec.lora_base(), reg)):
            jobs.append(PlannedJob(preview[0].id, preview[1].id, portrait_params(*preview)))
        return jobs

    def plan(self, inp: LoraTrainInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs = [StepSpec("dataset", StepKind.local, f"Pack {len(inp.photos)} photos", {
            "op": "lora_dataset", "element_id": inp.element_id, "photos": inp.photos, "title": f"@{inp.slug} training photos",
        })]
        picked = trainer(inp, reg)
        dataset = ok(settled, "dataset")
        if dataset and picked:
            spec, mode = picked
            specs.append(StepSpec("train", StepKind.generate, f"Learn @{inp.slug}'s face ({inp.steps} steps)", {
                "model_id": spec.id, "mode": mode.id, "prompt": "", "params": train_params(spec, mode, inp),
                "media": [{"role": "training_data", "asset_id": dataset["asset_id"]}], "direction": None,
            }, ("dataset",)))
        elif dataset and "train" not in settled:  # its trainer left the registry since the run started: end it
            raise ValueError(f"{inp.trainer_model_id} isn't a LoRA trainer in the registry")
        trained = ok(settled, "train")
        if trained:
            specs.append(StepSpec("attach", StepKind.local, f"Give @{inp.slug} the trained identity", {
                "op": "attach_lora", "element_id": inp.element_id, "trigger": inp.trigger, "steps": inp.steps,
                "assets": trained.get("asset_ids") or [trained["asset_id"]],
            }, ("train",)))
        # drawn on a model that loads the identity just attached; none left in the registry: the run ends without one
        attached = ok(settled, "attach")
        if inp.preview and attached and (preview := preview_model(attached.get("base"), reg)):
            specs.append(StepSpec("preview", StepKind.generate, f"A portrait of @{inp.slug}", {
                "model_id": preview[0].id, "mode": preview[1].id, "params": portrait_params(*preview), "media": [],
                "prompt": f"A natural portrait photo of @{inp.slug}, soft daylight, looking at the camera.",
                "direction": None,
            }, ("attach",), optional=True))
        return specs

    def result(self, inp: LoraTrainInput, settled: Settled) -> dict[str, Any] | None:
        attach = ok(settled, "attach")
        if not attach:
            return None
        preview = ok(settled, "preview")  # the run ends once every step planned has: a portrait planned is in by then
        return {"element_id": inp.element_id, "trigger": inp.trigger, "lora": attach,
                "asset_id": preview["asset_id"] if preview else None}
