"""A reference sheet for a saved character: one picture with it drawn from several sides, made from the pictures it
already has and kept as its second reference (right after its main picture). Models that take references attach a
character's first two pictures, so the sheet tells them its whole shape (a griffin's lion half too), where a single
picture shows one side and leaves the rest to guesswork.

The same recipe draws an expression sheet (eight feelings from the director's emotion wheel) or a pose sheet (eight
full-body poses), each a 4 x 2 grid kept with the character's pictures for people to see, never attached by a
mention."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.director.elements import SLUG
from studio.director.library import get_library
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mention_refs, with_param
from studio.pipelines.recipes.vlog import frame_mode
from studio.registry.loader import Registry

# every full-body view shows the whole body: a "wings spread" view once came back as a plain eagle, no lion half
SHEET = ("Character reference sheet of @{slug}: one plain light grey canvas showing the same character five times at "
         "the same scale, no text or labels. Four full-body views, each showing its whole body from head to tail and "
         "feet: side profile; front; three-quarter from behind; side profile with its wings, arms or other limbs raised "
         "wide. Then a close-up of its head. Every view has exactly the anatomy, proportions, colours and markings of "
         "its reference picture, and its medium: a photograph stays a photograph, with real texture and soft studio "
         "light.")
GRID = ("{what} of @{slug}: one plain light grey canvas divided into a grid of four columns and two rows, the same "
        "character {framing} in every cell at the same scale, with no text, labels, numbers or captions anywhere. Top "
        "row, left to right: {top}. Bottom row, left to right: {bottom}. Every cell has exactly the {same} of its "
        "reference picture, and its medium: a photograph stays a photograph, with real texture and soft studio light.")
# a grid sheet: what it's called, how each cell frames the character, and what must match its pictures
GRIDS = {"expressions": ("Expression sheet", "head and shoulders, facing the camera", "face, hair, colours and markings"),
         "poses": ("Pose sheet", "full body from head to feet", "anatomy, proportions, colours, clothing and markings")}
POSES = ("standing", "walking", "running", "sitting", "jumping", "pointing", "arms crossed", "waving")


def cells(sheet: str) -> list[str]:
    """The eight cells of a grid sheet: a plain feeling of each family of the emotion wheel (its middle level), or
    eight poses."""
    if sheet == "poses":
        return list(POSES)
    return [e.label.lower() for e in get_library().emotions if e.level == 2]


def sheet_prompt(sheet: str, slug: str) -> str:
    if sheet == "turnaround":
        return SHEET.format(slug=slug)
    what, framing, same = GRIDS[sheet]
    eight = cells(sheet)
    return GRID.format(what=what, slug=slug, framing=framing, top=", ".join(eight[:4]), bottom=", ".join(eight[4:8]),
                       same=same)


class CharacterSheetInput(BaseModel):
    element_id: uuid.UUID
    slug: str = Field(pattern=SLUG)
    sheet: Literal["turnaround", "expressions", "poses"] = Field(
        "turnaround", description="From several sides (kept as its reference), or a grid of expressions or poses")
    image_model_id: str = "nano-banana-pro"
    aspect_ratio: str = "16:9"


class CharacterSheetRecipe(Recipe):
    id = "character_sheet"
    name = "Reference sheet"
    summary = "A character drawn from several sides on one picture (kept as its reference), or its expressions or poses."
    Input = CharacterSheetInput

    def title(self, inp: CharacterSheetInput) -> str:
        return f"{GRIDS[inp.sheet][0] if inp.sheet in GRIDS else 'Reference sheet'} of @{inp.slug}"

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
        return [PlannedJob(model_id, mode_id, params, mention_refs(reg.models[model_id], mode_id, characters=1))]

    def plan(self, inp: CharacterSheetInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        model_id, mode_id, params = self._job(inp, reg)
        turnaround = inp.sheet == "turnaround"
        draw = f"Draw @{inp.slug} from every side" if turnaround else f"Draw @{inp.slug}'s {inp.sheet}"
        specs = [StepSpec("sheet", StepKind.generate, draw, {
            "model_id": model_id, "mode": mode_id, "prompt": sheet_prompt(inp.sheet, inp.slug), "params": params,
            "media": []})]  # the @mention attaches its pictures
        if out := ok(settled, "sheet"):
            # the turnaround goes right after the main picture, among the two a mention attaches; a grid never is
            keep, label = (({"view": "sheet", "first": True}, f"Keep it as @{inp.slug}'s reference") if turnaround
                           else ({"view": inp.sheet}, f"Keep it with @{inp.slug}'s pictures"))
            specs.append(StepSpec("keep", StepKind.local, label, {
                "op": "add_element_image", "element_id": str(inp.element_id), "asset_id": out["asset_id"], **keep},
                ("sheet",)))
        return specs

    def result(self, inp: CharacterSheetInput, settled: Settled) -> dict[str, Any] | None:
        kept = ok(settled, "keep")
        return {"asset_id": kept["asset_id"]} if kept else None
