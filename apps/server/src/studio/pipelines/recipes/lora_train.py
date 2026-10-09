"""Train a character's identity (Soul ID): zip the character's photos, train a Wan 2.2 LoRA on them, store it on the
character's Element, and draw one portrait with it. From then on, @mentioning the character on a model with the
`lora` element strategy (Wan 2.2 stills and video) loads the LoRA and writes its trigger word into the prompt."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.registry.loader import Registry

TRAINER = ("wan-2.2-lora-trainer", "character")
PREVIEW = ("wan-2.2-image", "t2i")
MIN_PHOTOS, MAX_PHOTOS = 10, 80
TRIGGER = re.compile(r"^[a-z][a-z0-9]{3,23}$")


class LoraTrainInput(BaseModel):
    element_id: str = Field(description="The character whose identity is learned")
    slug: str = Field(min_length=1, max_length=40, description="The character's @mention name, for the preview")
    photos: list[str] = Field(description=f"{MIN_PHOTOS}-{MAX_PHOTOS} picture asset ids of the character")
    trigger: str | None = Field(None, description="A made-up lowercase word the LoRA learns as this character; one is made from the name when left out")
    steps: int = Field(1000, ge=200, le=3000)
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


class LoraTrainRecipe(Recipe):
    id = "lora_train"
    name = "Train a character"
    summary = "Learn a character's face from their photos, so Wan 2.2 keeps it in every picture and clip."
    Input = LoraTrainInput

    def title(self, inp: LoraTrainInput) -> str:
        return f"Train @{inp.slug}"

    def validate(self, inp: LoraTrainInput, reg: Registry) -> list[str]:
        errors = []
        if not MIN_PHOTOS <= len(inp.photos) <= MAX_PHOTOS:
            errors.append(f"pick {MIN_PHOTOS} to {MAX_PHOTOS} photos of the character ({len(inp.photos)} given)")
        if not TRIGGER.match(inp.trigger or ""):
            errors.append("the trigger word must be 4-24 lowercase letters and digits, starting with a letter")
        for model_id, _ in (TRAINER, PREVIEW):
            if reg.model(model_id) is None:
                errors.append(f"{model_id} isn't in the registry")
        return errors

    def jobs(self, inp: LoraTrainInput, reg: Registry) -> list[PlannedJob]:
        jobs = [PlannedJob(*TRAINER, {"steps": inp.steps, "trigger_phrase": inp.trigger}, ("training_data",))]
        if inp.preview:
            jobs.append(PlannedJob(*PREVIEW, {"aspect_ratio": "3:4"}))
        return jobs

    def plan(self, inp: LoraTrainInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs = [StepSpec("dataset", StepKind.local, f"Pack {len(inp.photos)} photos", {
            "op": "lora_dataset", "photos": inp.photos, "title": f"@{inp.slug} training photos",
        })]
        dataset = ok(settled, "dataset")
        if dataset:
            specs.append(StepSpec("train", StepKind.generate, f"Learn @{inp.slug}'s face ({inp.steps} steps)", {
                "model_id": TRAINER[0], "mode": TRAINER[1], "prompt": "",
                "params": {"trigger_phrase": inp.trigger, "steps": inp.steps, "face_crop": inp.face_crop},
                "media": [{"role": "training_data", "asset_id": dataset["asset_id"]}], "direction": None,
            }, ("dataset",)))
        trained = ok(settled, "train")
        if trained:
            specs.append(StepSpec("attach", StepKind.local, f"Give @{inp.slug} the trained identity", {
                "op": "attach_lora", "element_id": inp.element_id, "trigger": inp.trigger, "steps": inp.steps,
                "assets": trained.get("asset_ids") or [trained["asset_id"]],
            }, ("train",)))
        if inp.preview and ok(settled, "attach"):
            specs.append(StepSpec("preview", StepKind.generate, f"A portrait of @{inp.slug}", {
                "model_id": PREVIEW[0], "mode": PREVIEW[1], "params": {"aspect_ratio": "3:4"}, "media": [],
                "prompt": f"A natural portrait photo of @{inp.slug}, soft daylight, looking at the camera.",
                "direction": None,
            }, ("attach",), optional=True))
        return specs

    def result(self, inp: LoraTrainInput, settled: Settled) -> dict[str, Any] | None:
        attach = ok(settled, "attach")
        if not attach or (inp.preview and "preview" not in settled):
            return None
        preview = ok(settled, "preview")
        return {"element_id": inp.element_id, "trigger": inp.trigger, "lora": attach,
                "asset_id": preview["asset_id"] if preview else None}
