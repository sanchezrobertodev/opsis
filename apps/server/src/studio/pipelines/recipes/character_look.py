"""A look for a saved character: an outfit, or the same character in another picture style, drawn from its pictures
and kept as the look's pictures. `@maya:winter` in a prompt then attaches those first and reads the look's description
after the character's; a plain `@maya` never does."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.director.elements import LOOK_SLUG, SLUG, clip, style_slug, unknown_styles
from studio.director.library import get_library
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.character_photos import ANGLES
from studio.pipelines.recipes.common import mention_refs, with_param
from studio.pipelines.recipes.vlog import frame_mode
from studio.registry.loader import Registry

PICTURE = (
    "Full-body picture of @{slug}{wearing}, standing, {angle}, head to feet in frame, even studio light on a "
    "plain light grey background. Exactly the same {same} as the reference picture{medium}. Only @{slug} in "
    "frame, no text."
)


class CharacterLookInput(BaseModel):
    character: str = Field(pattern=SLUG, description="The saved character's @handle")
    element_id: uuid.UUID | None = Field(None, description="Filled in from the handle when the run is made")
    look: str = Field(pattern=LOOK_SLUG, description="The look's handle: `@character:look` in a prompt")
    name: str = Field(min_length=1, max_length=40, description="What the look is called")
    description: str = Field(
        "",
        max_length=160,
        description="How a prompt reads the look, after the character's own "
        'description ("wearing a red wool coat and boots"); from the outfit when empty',
    )
    outfit: str = Field("", max_length=300, description="What they wear in it")
    style: str = Field(
        "",
        max_length=41,
        description="A picture style to draw it in: a style catalog id, or "
        "@handle for one of your saved styles",
    )
    model_id: str = Field("nano-banana-pro", description="An image model that draws from reference pictures")
    count: int = Field(1, ge=1, le=4)
    aspect_ratio: str = "3:4"


def worded(inp: CharacterLookInput) -> str:
    """How a prompt reads the look: as given, else what it wears."""
    outfit = " ".join(inp.outfit.split())
    return " ".join(inp.description.split()) or (
        clip(f"wearing {outfit}", 160, at_comma=True) if outfit else ""
    )


class CharacterLookRecipe(Recipe):
    id = "character_look"
    name = "A look"
    summary = (
        "A saved character in another outfit or picture style, kept as a look to mention as @handle:look."
    )
    Input = CharacterLookInput

    def title(self, inp: CharacterLookInput) -> str:
        return f"{inp.name} look for @{inp.character}"

    async def resolve(
        self, session: Any, user_id: uuid.UUID, inp: CharacterLookInput, reg: Registry
    ) -> CharacterLookInput:
        """It's drawn from the character's pictures: the character must still exist and have one (its id is written
        into the input), and a saved style must be one of yours."""
        from studio.domain.generations import GenerationRequestError
        from studio.pipelines.llm_ops import find_star  # local: llm_ops imports the recipes

        element, problem = await find_star(session, user_id, inp.character)
        if element is None or inp.element_id not in (None, element.id):
            raise GenerationRequestError([problem or f"@{inp.character} isn't that character any more"])
        if (slug := style_slug(inp.style)) and await unknown_styles(session, user_id, [slug]):
            raise GenerationRequestError([f"unknown style @{slug}"])
        return inp.model_copy(update={"element_id": element.id})

    def validate(self, inp: CharacterLookInput, reg: Registry) -> list[str]:
        errors = []
        spec = reg.model(inp.model_id)
        if spec is None or frame_mode(spec) is None:
            errors.append(f"{inp.model_id!r} can't draw from reference pictures")
        if inp.style and not style_slug(inp.style) and get_library().look("styles", inp.style) is None:
            errors.append(f"unknown style {inp.style!r}")
        if not inp.outfit.strip() and not inp.style:
            errors.append("say what the look wears, or pick a style to draw it in")
        return errors

    def _job(self, inp: CharacterLookInput, reg: Registry) -> tuple[str, str, dict[str, Any]]:
        spec = reg.models[inp.model_id]
        mode = frame_mode(spec)
        assert mode is not None  # validate() checked
        return spec.id, mode.id, with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def jobs(self, inp: CharacterLookInput, reg: Registry) -> list[PlannedJob]:
        model_id, mode_id, params = self._job(inp, reg)
        # what the @mention attaches, and a saved style's pictures
        roles = mention_refs(reg.models[model_id], mode_id, characters=1, style=inp.style)
        return [PlannedJob(model_id, mode_id, params, roles) for _ in range(inp.count)]

    def plan(self, inp: CharacterLookInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        model_id, mode_id, params = self._job(inp, reg)
        outfit = " ".join(inp.outfit.split())
        keep = {
            "op": "add_element_image",
            "element_id": str(inp.element_id),
            "look": inp.look,
            "look_name": inp.name.strip(),
            "look_description": worded(inp),
        }
        specs = []
        for i in range(inp.count):
            angle, view = ANGLES[i]
            prompt = PICTURE.format(
                slug=inp.character,
                wearing=f", wearing {outfit}" if outfit else "",
                angle=angle,
                same="face, hair, build and skin" if outfit else "face, hair, build, skin and clothing",
                # a style says what it's drawn in: then a photograph needn't stay one
                medium="" if inp.style else ", in its medium and style: a photograph stays a photograph",
            )
            key = f"look[{i:02d}]"
            # the @mention attaches its pictures, and a saved style (@handle) its own where the mode takes references
            specs.append(
                StepSpec(
                    key,
                    StepKind.generate,
                    f"Draw @{inp.character} as {inp.name}, {angle}",
                    {
                        "model_id": model_id,
                        "mode": mode_id,
                        "prompt": prompt,
                        "params": params,
                        "media": [],
                        **({"direction": {"style": inp.style}} if inp.style else {}),
                    },
                    optional=True,
                )
            )
            if out := ok(settled, key):
                specs.append(
                    StepSpec(
                        f"keep[{i:02d}]",
                        StepKind.local,
                        f"Keep it as @{inp.character}:{inp.look}",
                        {**keep, "asset_id": out["asset_id"], "view": view},
                        (key,),
                    )
                )
        return specs

    def result(self, inp: CharacterLookInput, settled: Settled) -> dict[str, Any] | None:
        """Done once every picture is drawn or given up on (they're optional) and every drawn one is kept."""
        pics = [settled.get(f"look[{i:02d}]") for i in range(inp.count)]
        if any(p is None for p in pics):
            return None
        kept = [
            settled.get(f"keep[{i:02d}]") for i, p in enumerate(pics) if p is not None and "failed" not in p
        ]
        if not kept or any(k is None for k in kept):
            return None
        return {"look": inp.look, "asset_ids": [k["asset_id"] for k in kept if k]}

    def failure(self, inp: CharacterLookInput, settled: Settled) -> str | None:
        pics = [settled.get(f"look[{i:02d}]") for i in range(inp.count)]
        if all(p is not None and "failed" in p for p in pics):
            return "none of the pictures could be drawn"
        return None
