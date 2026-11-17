"""A 3D figure of a saved character, to pose and move in 3D Jutsu.

  pose (a reference mode draws them head to feet in an A-pose; skipped when the input names one of their own
  pictures) -> model (a picture-to-3D mode) -> rig (optional: a skeleton with walking and running clips; off by
  default for a non-human creature) -> keep (the GLBs carry the character's `element_id` and handle in their
  metadata, for finding a character's figures later; nothing reads them yet)

Modes are picked by what they do, never by id: the image model's reference mode, a `to_3d` mode whose one required
input is a picture, and a `rig` mode that takes a 3D model.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from studio.characters.traits import CREATURE
from studio.db.models import StepKind
from studio.director.elements import SLUG, UNATTACHED_VIEWS
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mention_refs, with_param
from studio.pipelines.recipes.vlog import frame_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

POSE = (
    "Full-body picture of @{slug} standing in a relaxed A-pose, facing the camera: arms held a little away from the "
    "body, legs slightly apart, head to feet in frame with space around them. Even, soft light on a plain white "
    "background, no shadows. Exactly the same face, hair, build, skin and clothing as the reference pictures. Only "
    "@{slug} in frame, no props, no text."
)


def figure_mode(spec: ModelSpec) -> tuple[Mode, str] | None:
    """A picture-to-3D mode: op `to_3d`, one required input that takes a single picture, no prompt needed. Returns it
    and the picture's role."""
    for m in spec.modes:
        required = [s for s in m.medias if s.required]
        if (
            "to_3d" in m.ops
            and m.prompt != "required"
            and len(required) == 1
            and "image" in required[0].kinds
            and required[0].min <= 1
        ):
            return m, required[0].role
    return None


def rig_mode(spec: ModelSpec) -> tuple[Mode, str] | None:
    """A mode that rigs a 3D model (op `rig`), and the model's role."""
    for m in spec.modes:
        required = [s for s in m.medias if s.required]
        if "rig" in m.ops and len(required) == 1 and "model3d" in required[0].kinds:
            return m, required[0].role
    return None


class CharacterFigureInput(BaseModel):
    character: str = Field(pattern=SLUG, description="The saved character's @handle")
    element_id: uuid.UUID | None = Field(None, description="Filled in from the handle when the run is made")
    picture_asset_id: uuid.UUID | None = Field(
        None,
        description="One of the character's own full-body pictures to build from, instead of drawing one",
    )
    image_model_id: str = Field(
        "nano-banana-pro", description="An image model that draws from reference pictures"
    )
    model_id: str = Field("tripo-h3.1", description="A model that builds a 3D model from one picture")
    rig: bool | None = Field(
        None, description="Give it a skeleton so it can move; by default yes, except for a non-human creature"
    )
    rig_model_id: str = Field("meshy-rigging", description="A model that rigs a 3D character")
    aspect_ratio: str = "3:4"


class CharacterFigureRecipe(Recipe):
    id = "character_figure"
    name = "3D figure"
    summary = "A saved character as a 3D model (GLB), rigged to move, for 3D Jutsu."
    Input = CharacterFigureInput

    def title(self, inp: CharacterFigureInput) -> str:
        return f"3D figure of @{inp.character}"

    async def resolve(
        self, session: Any, user_id: uuid.UUID, inp: CharacterFigureInput, reg: Registry
    ) -> CharacterFigureInput:
        """The character must exist and have a picture (its id is written into the input); a picture given must be
        one of its main ones; and whether to rig, when unsaid: not for a non-human creature."""
        from studio.db.models import Asset, ElementAsset  # models import nothing from recipes
        from studio.domain.enums import AssetStatus
        from studio.domain.generations import GenerationRequestError
        from studio.pipelines.llm_ops import find_star  # local: llm_ops imports the recipes

        element, problem = await find_star(session, user_id, inp.character)
        if element is None or inp.element_id not in (None, element.id):
            raise GenerationRequestError([problem or f"@{inp.character} isn't that character any more"])
        if (
            inp.picture_asset_id is not None
            and (
                await session.execute(
                    select(ElementAsset.asset_id)
                    .join(Asset, Asset.id == ElementAsset.asset_id)
                    .where(
                        ElementAsset.element_id == element.id,
                        ElementAsset.asset_id == inp.picture_asset_id,
                        ElementAsset.look.is_(None),
                        ElementAsset.view.not_in(UNATTACHED_VIEWS),
                        Asset.kind == "image",
                        Asset.status == AssetStatus.ready,
                        Asset.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            is None
        ):
            raise GenerationRequestError([f"that picture isn't one of @{inp.character}'s own pictures"])
        rig = inp.rig
        if rig is None:
            traits = (element.attributes or {}).get("traits")
            rig = not (isinstance(traits, dict) and traits.get("presentation") == CREATURE)
        return inp.model_copy(update={"element_id": element.id, "rig": rig})

    def validate(self, inp: CharacterFigureInput, reg: Registry) -> list[str]:
        errors = []
        checks = [(inp.model_id, figure_mode, "build a 3D model from a picture")]
        if inp.picture_asset_id is None:
            checks.append((inp.image_model_id, frame_mode, "draw from reference pictures"))
        if inp.rig:
            checks.append((inp.rig_model_id, rig_mode, "rig a 3D character"))
        for model_id, find, what in checks:
            spec = reg.model(model_id)
            if spec is None or find(spec) is None:
                errors.append(f"{spec.name if spec else repr(model_id)} can't {what}")
        return errors

    def _pose(self, inp: CharacterFigureInput, reg: Registry) -> tuple[ModelSpec, Mode, dict[str, Any]]:
        spec = reg.models[inp.image_model_id]
        mode = frame_mode(spec)
        assert mode is not None  # validate() checked
        return spec, mode, with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def jobs(self, inp: CharacterFigureInput, reg: Registry) -> list[PlannedJob]:
        out = []
        if inp.picture_asset_id is None:
            spec, mode, params = self._pose(inp, reg)
            # the @mention attaches the character's pictures, priced like pictures of yours
            out.append(PlannedJob(spec.id, mode.id, params, mention_refs(spec, mode.id, characters=1)))
        figure = reg.models[inp.model_id]
        mode, role = figure_mode(figure)  # type: ignore[misc]
        out.append(PlannedJob(figure.id, mode.id, {}, (role,)))
        if inp.rig:
            rigger = reg.models[inp.rig_model_id]
            rmode, rrole = rig_mode(rigger)  # type: ignore[misc]
            out.append(PlannedJob(rigger.id, rmode.id, {}, (rrole,)))
        return out

    def plan(self, inp: CharacterFigureInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs: list[StepSpec] = []
        picture, deps = (str(inp.picture_asset_id), ()) if inp.picture_asset_id else (None, ("pose",))
        if picture is None:
            spec, mode, params = self._pose(inp, reg)
            # the @mention attaches the character's main pictures
            specs.append(
                StepSpec(
                    "pose",
                    StepKind.generate,
                    f"Draw @{inp.character} head to feet in an A-pose",
                    {
                        "model_id": spec.id,
                        "mode": mode.id,
                        "prompt": POSE.format(slug=inp.character),
                        "params": params,
                        "media": [],
                    },
                )
            )
            drawn = ok(settled, "pose")
            if drawn is None:
                return specs
            picture = drawn["asset_id"]
        figure = reg.models[inp.model_id]
        mode, role = figure_mode(figure)  # type: ignore[misc]
        specs.append(
            StepSpec(
                "model",
                StepKind.generate,
                f"Build the 3D model ({figure.name})",
                {
                    "model_id": figure.id,
                    "mode": mode.id,
                    "prompt": "",
                    "params": {},
                    "media": [{"role": role, "asset_id": picture}],
                },
                deps,
            )
        )
        made = ok(settled, "model")
        if made is None:
            return specs
        glbs, after = list(made["asset_ids"]), ("model",)
        if inp.rig:
            rigger = reg.models[inp.rig_model_id]
            rmode, rrole = rig_mode(rigger)  # type: ignore[misc]
            # optional: a figure the rigger can't read as a body (refused at pose estimation) is still kept, unrigged
            specs.append(
                StepSpec(
                    "rig",
                    StepKind.generate,
                    f"Rig it to move ({rigger.name})",
                    {
                        "model_id": rigger.id,
                        "mode": rmode.id,
                        "prompt": "",
                        "params": {},
                        "media": [{"role": rrole, "asset_id": made["asset_id"]}],
                    },
                    ("model",),
                    optional=True,
                )
            )
            if "rig" not in settled:
                return specs
            glbs, after = glbs + list((ok(settled, "rig") or {}).get("asset_ids") or []), ("model", "rig")
        specs.append(
            StepSpec(
                "keep",
                StepKind.local,
                f"Keep it with @{inp.character}",
                {
                    "op": "tag_assets",
                    "asset_ids": glbs,
                    "meta": {"element_id": str(inp.element_id), "character": inp.character},
                },
                after,
            )
        )
        return specs

    def result(self, inp: CharacterFigureInput, settled: Settled) -> dict[str, Any] | None:
        made, kept = ok(settled, "model"), ok(settled, "keep")
        if made is None or kept is None:
            return None
        rigged = ok(settled, "rig")
        return {
            "asset_id": (rigged or made)["asset_id"],
            "model_asset_id": made["asset_id"],
            "rigged": rigged is not None,
            "element_id": str(inp.element_id),
        }
