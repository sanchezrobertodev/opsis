"""Face swap: the person in a picture or a clip becomes the person in a photo, or a saved character.

  a picture: swap (a reference-editing image model, `multi_ref`: the picture and the face, with an instruction)
  a clip:    swap (a `person_swap` mode: the clip and the face; PixVerse Swap, H3 Max Recast or Wan Animate replace)

A saved character stands in with their main picture, found when the run starts (`resolve`). Only swap people who
agreed: the input carries that consent, like a voice clone's.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_with_op
from studio.pipelines.recipes.vlog import SLUG
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

PICTURE_PROMPT = (
    # the head, not just the face: hair is much of how a person reads, and a face-only edit barely shows (live check)
    "Put the person from Image 2 into Image 1: replace the head of the person in Image 1, their face, facial features, "
    "skin tone, eye color, hairstyle and hair color, with the person in Image 2's, so they are clearly recognizable as "
    "the person in Image 2. Keep Image 1's body, pose, clothes, framing, lighting, colors and background exactly as they "
    "are, and match the new head to Image 1's light, angle and expression."
)


class FaceSwapInput(BaseModel):
    picture_asset_id: uuid.UUID | None = Field(None, description="A picture with a person")
    video_asset_id: uuid.UUID | None = Field(None, description="Or a clip with a person")
    face_asset_id: uuid.UUID | None = Field(None, description="A clear photo of the new person's face")
    character: str | None = Field(None, pattern=SLUG, description="Or a saved character's @handle")
    consent: bool = Field(False, description="The people in both agreed to the swap (required)")
    image_model: str = "nano-banana-pro"
    swap_model: str = "pixverse-swap"
    picture_aspect: float | None = Field(None, description="Filled in when the run starts: the picture's width / height")

    @model_validator(mode="after")
    def _one_each(self) -> FaceSwapInput:
        if not self.consent:
            raise ValueError("only swap people who agreed: confirm their consent")
        if (self.picture_asset_id is None) == (self.video_asset_id is None):
            raise ValueError("give a picture or a clip, not both")
        if self.face_asset_id is None and self.character is None:
            raise ValueError("give a photo of the new face or pick a saved character")
        return self


def _slot(mode: Mode, kind: str) -> str | None:
    """The role of the mode's first slot that takes `kind` (the clip, or the new person's photo)."""
    return next((s.role for s in mode.medias if kind in s.kinds and not s.auto), None)


def keep_shape(spec: ModelSpec, aspect: float | None) -> dict[str, Any]:
    """The model's aspect choice nearest the picture's, so the swap comes back the picture's shape."""
    param = next((p for p in spec.parameters if p.name == "aspect_ratio" and p.type == "enum"), None)
    choices = [o for o in getattr(param, "options", []) if isinstance(o, str) and ":" in o]
    if not aspect or not choices:
        return {}
    ratio = lambda o: int(o.split(":")[0]) / int(o.split(":")[1])  # noqa: E731
    return {"aspect_ratio": min(choices, key=lambda o: abs(math.log(ratio(o) / aspect)))}


def swap_mode(spec: ModelSpec) -> Mode | None:
    """A mode that swaps the person in a clip: the `person_swap` op with a clip slot and a photo slot."""
    m = mode_with_op(spec, "person_swap")
    return m if m and _slot(m, "video") and _slot(m, "image") else None


class FaceSwapRecipe(Recipe):
    id = "face_swap"
    name = "Face swap"
    summary = "The person in a picture or a clip becomes the person in your photo, or one of your characters."
    Input = FaceSwapInput

    def title(self, inp: FaceSwapInput) -> str:
        return f"Face swap: {'@' + inp.character if inp.character else 'a photo'}"

    async def resolve(self, session: AsyncSession, user_id: uuid.UUID, inp: FaceSwapInput,
                      reg: Registry) -> FaceSwapInput:
        """A saved character's face is their main picture (else their cover), and the picture's shape, written
        into the input."""
        from studio.db.models import (  # models import nothing from recipes
            Asset,
            Element,
            ElementAsset,
            ElementKind,
        )

        if inp.picture_asset_id is not None:
            w, h = (await session.execute(select(Asset.width, Asset.height).where(
                Asset.id == inp.picture_asset_id, Asset.user_id == user_id))).one_or_none() or (None, None)
            if w and h:
                inp = inp.model_copy(update={"picture_aspect": w / h})
        if inp.character is None or inp.face_asset_id is not None:
            return inp

        element = (await session.execute(select(Element).where(
            Element.user_id == user_id, Element.slug == inp.character, Element.kind == ElementKind.character,
            Element.deleted_at.is_(None)))).scalar_one_or_none()
        if element is None:
            return inp
        face = (await session.execute(select(ElementAsset.asset_id).where(ElementAsset.element_id == element.id)
                                      .order_by(ElementAsset.is_primary.desc(), ElementAsset.sort).limit(1)
                                      )).scalar_one_or_none() or element.cover_asset_id
        return inp.model_copy(update={"face_asset_id": face}) if face else inp

    def validate(self, inp: FaceSwapInput, reg: Registry) -> list[str]:
        errors = []
        if inp.face_asset_id is None:
            errors.append(f"@{inp.character} has no picture to take the face from")
        if inp.picture_asset_id:
            spec = reg.model(inp.image_model)
            if spec is None or spec.output_type != "image" or mode_with_op(spec, "multi_ref") is None:
                errors.append(f"{inp.image_model!r} can't edit a picture with a reference")
        else:
            spec = reg.model(inp.swap_model)
            if spec is None or swap_mode(spec) is None:
                errors.append(f"{inp.swap_model!r} can't swap the person in a clip")
        return errors

    def _job(self, inp: FaceSwapInput, reg: Registry) -> tuple[ModelSpec, Mode, list[tuple[str, str]], str]:
        """The one generation: its model, mode, inputs (role, asset id) in order, and prompt (params: `_params`)."""
        face = str(inp.face_asset_id)
        if inp.picture_asset_id:
            spec = reg.models[inp.image_model]
            mode = mode_with_op(spec, "multi_ref")
            role = _slot(mode, "image")  # type: ignore[arg-type]
            return spec, mode, [(role, str(inp.picture_asset_id)), (role, face)], PICTURE_PROMPT  # type: ignore[list-item,return-value]
        spec = reg.models[inp.swap_model]
        mode = swap_mode(spec)
        return spec, mode, [(_slot(mode, "video"), str(inp.video_asset_id)), (_slot(mode, "image"), face)], ""  # type: ignore[arg-type,list-item,return-value]

    def jobs(self, inp: FaceSwapInput, reg: Registry) -> list[PlannedJob]:
        if inp.face_asset_id is None:
            return []
        spec, mode, media, _ = self._job(inp, reg)
        return [PlannedJob(spec.id, mode.id, self._params(inp, spec), tuple(dict.fromkeys(r for r, _ in media)),
                           media=tuple(media))]

    def _params(self, inp: FaceSwapInput, spec: ModelSpec) -> dict[str, Any]:
        return keep_shape(spec, inp.picture_aspect) if inp.picture_asset_id else {}

    def plan(self, inp: FaceSwapInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec, mode, media, prompt = self._job(inp, reg)
        return [StepSpec("swap", StepKind.generate, "Swap the face", {
            "model_id": spec.id, "mode": mode.id, "prompt": prompt, "params": self._params(inp, spec),
            "media": [{"role": r, "asset_id": a} for r, a in media]})]

    def result(self, inp: FaceSwapInput, settled: Settled) -> dict[str, Any] | None:
        done = ok(settled, "swap")
        return {"asset_id": done["asset_id"]} if done else None
