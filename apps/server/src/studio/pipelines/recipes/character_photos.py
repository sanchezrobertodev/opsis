"""More pictures of a saved character: the same face drawn from other angles, with other expressions, light and
framing, each kept as one of its pictures. A trained identity needs 10 or more varied photos; this makes them from
the few it has (the @mention attaches its first two pictures to each one)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import with_param
from studio.pipelines.recipes.vlog import SLUG, frame_mode
from studio.registry.loader import Registry

Vary = Literal["angle", "expression", "light", "framing"]

# (words, the view it's kept as); the first of each list is what stays when that aspect isn't varied
ANGLES = (("facing the camera", "front"), ("three-quarter view, turned to the left", "three_quarter"),
          ("three-quarter view, turned to the right", "three_quarter"), ("side profile", "side"),
          ("seen from behind, looking back over the shoulder", "back"), ("seen from slightly above", "front"),
          ("seen from slightly below", "front"))
EXPRESSIONS = ("a neutral expression", "a warm smile", "laughing", "serious and focused", "surprised",
               "thoughtful, looking away", "determined")
LIGHTS = ("even studio light on a plain light grey background", "soft window light", "golden-hour sunlight outdoors",
          "overcast daylight outdoors", "warm lamp light indoors", "cool blue evening light", "dramatic side light")
FRAMINGS = ("head and shoulders", "waist-up", "full body")

PICTURE = ("Picture of @{slug}, {framing}, {angle}, {expression}, {light}. Exactly the same face, hair, build, skin, "
           "colours and clothing as the reference picture, in its medium and style: a photograph stays a photograph. "
           "Only @{slug} in frame, no text.")


def variation(i: int, vary: set[str]) -> tuple[str, str, str, str, str]:
    """The i-th picture's angle, expression, light, framing and view. Different strides over lists of 7 mix them, so
    neighbouring pictures differ in every varied aspect."""
    angle, view = ANGLES[i % 7] if "angle" in vary else ANGLES[0]
    return (angle, EXPRESSIONS[(3 * i + 1) % 7] if "expression" in vary else EXPRESSIONS[0],
            LIGHTS[(5 * i + 2) % 7] if "light" in vary else LIGHTS[0],
            FRAMINGS[i % 3] if "framing" in vary else FRAMINGS[0], view)


class CharacterPhotosInput(BaseModel):
    element_id: uuid.UUID
    slug: str = Field(pattern=SLUG)
    count: int = Field(8, ge=1, le=20)
    vary: list[Vary] = Field(default_factory=lambda: ["angle", "expression", "light", "framing"])
    image_model_id: str = "nano-banana-pro"
    aspect_ratio: str = "3:4"


class CharacterPhotosRecipe(Recipe):
    id = "character_photos"
    name = "More pictures"
    summary = "A saved character drawn from other angles, with other expressions and light, kept as its pictures."
    Input = CharacterPhotosInput

    def title(self, inp: CharacterPhotosInput) -> str:
        return f"{inp.count} more pictures of @{inp.slug}"

    async def resolve(self, session: Any, user_id: uuid.UUID, inp: CharacterPhotosInput,
                      reg: Registry) -> CharacterPhotosInput:
        """They're drawn from the character's pictures: it must still exist and have one."""
        from studio.domain.generations import GenerationRequestError
        from studio.pipelines.llm_ops import find_star  # local: llm_ops imports the recipes

        element, problem = await find_star(session, user_id, inp.slug)
        if element is None or element.id != inp.element_id:
            raise GenerationRequestError([problem or f"@{inp.slug} isn't that character any more"])
        return inp

    def validate(self, inp: CharacterPhotosInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.image_model_id)
        if spec is None or frame_mode(spec) is None:
            return [f"{inp.image_model_id!r} can't draw from reference pictures"]
        return []

    def _job(self, inp: CharacterPhotosInput, reg: Registry) -> tuple[str, str, dict[str, Any]]:
        spec = reg.models[inp.image_model_id]
        mode = frame_mode(spec)
        assert mode is not None  # validate() checked
        return spec.id, mode.id, with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def jobs(self, inp: CharacterPhotosInput, reg: Registry) -> list[PlannedJob]:
        model_id, mode_id, params = self._job(inp, reg)
        role = (reg.models[model_id].elements or {}).get("ref_role", "image_references")
        return [PlannedJob(model_id, mode_id, params, (role,)) for _ in range(inp.count)]

    def plan(self, inp: CharacterPhotosInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        model_id, mode_id, params = self._job(inp, reg)
        specs = []
        for i in range(inp.count):
            angle, expression, light, framing, view = variation(i, set(inp.vary))
            key = f"pic[{i:02d}]"
            specs.append(StepSpec(key, StepKind.generate, f"@{inp.slug}, {angle}, {expression}", {
                "model_id": model_id, "mode": mode_id, "params": params, "media": [],  # the @mention attaches its pictures
                "prompt": PICTURE.format(slug=inp.slug, angle=angle, expression=expression, light=light,
                                         framing=framing)}, optional=True))
            if out := ok(settled, key):
                specs.append(StepSpec(f"keep[{i:02d}]", StepKind.local, f"Keep it as one of @{inp.slug}'s pictures", {
                    "op": "add_element_image", "element_id": str(inp.element_id), "asset_id": out["asset_id"],
                    "view": view}, (key,)))
        return specs

    def result(self, inp: CharacterPhotosInput, settled: Settled) -> dict[str, Any] | None:
        """Done once every picture is drawn or given up on (they're optional) and every drawn one is kept."""
        pics = [settled.get(f"pic[{i:02d}]") for i in range(inp.count)]
        if any(p is None for p in pics):
            return None
        kept = [settled.get(f"keep[{i:02d}]") for i, p in enumerate(pics) if p is not None and "failed" not in p]
        if not kept or any(k is None for k in kept):
            return None
        return {"asset_ids": [k["asset_id"] for k in kept if k]}

    def failure(self, inp: CharacterPhotosInput, settled: Settled) -> str | None:
        pics = [settled.get(f"pic[{i:02d}]") for i in range(inp.count)]
        if all(p is not None and "failed" in p for p in pics):
            return "none of the pictures could be drawn"
        return None
