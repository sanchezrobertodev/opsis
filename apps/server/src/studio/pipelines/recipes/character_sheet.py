"""A reference sheet for a saved character: one picture with it drawn from several sides, made from the pictures it
already has and kept as its second reference (right after its main picture). Models that take references attach a
character's first two pictures, so the sheet tells them its whole shape (a griffin's lion half too), where a single
picture shows one side and leaves the rest to guesswork."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import with_param
from studio.pipelines.recipes.vlog import SLUG, frame_mode
from studio.registry.loader import Registry

SHEET = ("Character reference sheet of @{slug} on one plain light grey canvas, no text or labels: the same character "
         "five times at the same scale, side by side: full body in side profile, full body from the front, full body "
         "from behind at three-quarters, full body with everything it has spread out (wings, tail, limbs), and a "
         "close-up of the head. Identical anatomy, proportions, colours and markings in every view, in the style of "
         "its reference picture; soft even light, sharp focus.")


class CharacterSheetInput(BaseModel):
    element_id: uuid.UUID
    slug: str = Field(pattern=SLUG)
    image_model_id: str = "nano-banana-pro"
    aspect_ratio: str = "16:9"


class CharacterSheetRecipe(Recipe):
    id = "character_sheet"
    name = "Reference sheet"
    summary = "A character drawn from several sides on one picture, kept as its reference."
    Input = CharacterSheetInput

    def title(self, inp: CharacterSheetInput) -> str:
        return f"Reference sheet of @{inp.slug}"

    async def resolve(self, session: Any, user_id: uuid.UUID, inp: CharacterSheetInput,
                      reg: Registry) -> CharacterSheetInput:
        """The sheet is drawn from the character's pictures: it must still exist and have one."""
        from studio.domain.generations import GenerationRequestError
        from studio.pipelines.llm_ops import find_star  # local: llm_ops imports the recipes

        element, problem = await find_star(session, user_id, inp.slug)
        if element is None or element.id != inp.element_id:
            raise GenerationRequestError([problem or f"@{inp.slug} isn't that character any more"])
        return inp

    def validate(self, inp: CharacterSheetInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.image_model_id)
        if spec is None or frame_mode(spec) is None:
            return [f"{inp.image_model_id!r} can't draw from reference pictures"]
        return []

    def _job(self, inp: CharacterSheetInput, reg: Registry) -> tuple[str, str, dict[str, Any]]:
        spec = reg.models[inp.image_model_id]
        mode = frame_mode(spec)
        assert mode is not None  # validate() checked
        return spec.id, mode.id, with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def jobs(self, inp: CharacterSheetInput, reg: Registry) -> list[PlannedJob]:
        model_id, mode_id, params = self._job(inp, reg)
        role = (reg.models[model_id].elements or {}).get("ref_role", "image_references")
        return [PlannedJob(model_id, mode_id, params, (role,))]

    def plan(self, inp: CharacterSheetInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        model_id, mode_id, params = self._job(inp, reg)
        specs = [StepSpec("sheet", StepKind.generate, f"Draw @{inp.slug} from every side", {
            "model_id": model_id, "mode": mode_id, "prompt": SHEET.format(slug=inp.slug), "params": params,
            "media": []})]  # the @mention attaches its pictures
        if out := ok(settled, "sheet"):
            specs.append(StepSpec("keep", StepKind.local, f"Keep it as @{inp.slug}'s reference", {
                "op": "add_element_image", "element_id": str(inp.element_id), "asset_id": out["asset_id"],
                "view": "sheet", "first": True}, ("sheet",)))
        return specs

    def result(self, inp: CharacterSheetInput, settled: Settled) -> dict[str, Any] | None:
        kept = ok(settled, "keep")
        return {"asset_id": kept["asset_id"]} if kept else None
