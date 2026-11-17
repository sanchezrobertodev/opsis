"""Compare models: one prompt on several models side by side, then pick the take you like.

The same recipe tries one prompt in several styles: each entry lays its own direction (a picture style) over the
shared one and names its take (`label`)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import StepKind
from studio.director.compiler import ShotDirection
from studio.director.elements import IMAGES_PER_KIND, style_slug, unknown_styles
from studio.director.library import get_library
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_for
from studio.registry.loader import Registry


class CompareEntry(BaseModel):
    model_id: str
    mode: str | None = Field(None, description="Defaults to the mode that fits the media given")
    params: dict[str, Any] = {}
    direction: dict[str, Any] | None = Field(None, description="Laid over the shared direction for this take, e.g. "
                                             "{style} to try one prompt in several styles")
    label: str = Field("", max_length=80, description="The take's name in the run and the pick (the model's name "
                                                     "if empty)")


class CompareInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    entries: list[CompareEntry] = Field(min_length=2, max_length=6)
    media: list[dict[str, str]] = Field(default_factory=list, description="[{role, asset_id}], shared by every model")
    direction: dict[str, Any] | None = None
    ops: list[str] = Field(default_factory=list, description="What the compared modes do (e.g. restyle); picks "
                                                           "each model's matching mode")
    pick: bool = Field(True, description="Ask which take is best once they are all done")


def take_key(i: int, model_id: str) -> str:
    return f"take[{i}:{model_id}]"


def take_direction(inp: CompareInput, entry: CompareEntry) -> dict[str, Any] | None:
    # an entry without a direction of its own plans exactly as before, so older runs keep their steps
    return {**(inp.direction or {}), **entry.direction} if entry.direction else inp.direction


class CompareRecipe(Recipe):
    id = "compare"
    name = "Compare models"
    summary = "The same prompt on several models, or in several styles, at once, side by side."
    Input = CompareInput

    def title(self, inp: CompareInput) -> str:
        return f"Compare: {inp.prompt[:80]}"

    def _mode(self, inp: CompareInput, reg: Registry, entry: CompareEntry) -> str | None:
        spec = reg.model(entry.model_id)
        if spec is None:
            return None
        if entry.mode:
            return entry.mode if spec.mode(entry.mode) else None
        roles = {x["role"] for x in inp.media}
        if inp.ops:
            # same job, and every required input is on hand
            fits = [m for m in spec.modes if set(m.ops) & set(inp.ops)
                    and all(s.role in roles for s in m.medias if s.required)]
            return fits[0].id if fits else None
        m = mode_for(spec, roles)
        return m.id if m else None

    def _label(self, reg: Registry, entry: CompareEntry) -> str:
        return entry.label or reg.models[entry.model_id].name

    def _media(self, inp: CompareInput, reg: Registry, entry: CompareEntry, mode_id: str) -> list[dict[str, str]]:
        mode = reg.models[entry.model_id].mode(mode_id)
        assert mode is not None
        return [m for m in inp.media if mode.slot(m["role"])]

    def validate(self, inp: CompareInput, reg: Registry) -> list[str]:
        errors = []
        types = set()
        for e in inp.entries:
            spec = reg.model(e.model_id)
            if spec is None:
                errors.append(f"unknown model {e.model_id!r}")
                continue
            types.add(spec.output_type)
            if self._mode(inp, reg, e) is None:
                errors.append(f"{spec.name} has no mode for this prompt and media")
            if not (direction := take_direction(inp, e)):
                continue
            try:
                style = ShotDirection.model_validate(direction).style
            except ValidationError as exc:
                errors += [f"direction: {x['msg']} at {'.'.join(map(str, x['loc']))}" for x in exc.errors()]
                continue
            # a typo must not quietly make plain takes; saved styles (@handle) are checked in resolve()
            if style and not style_slug(style) and get_library().look("styles", style) is None:
                errors.append(f"unknown style {style!r}")
        if len(types) > 1:
            errors.append("compare models that make the same kind of output (all video or all image)")
        return list(dict.fromkeys(errors))

    async def resolve(self, session: AsyncSession, user_id: uuid.UUID, inp: CompareInput,
                      reg: Registry) -> CompareInput:
        from studio.domain.generations import GenerationRequestError  # local: it imports the registry

        slugs = [s for e in inp.entries if (s := style_slug((take_direction(inp, e) or {}).get("style")))]
        if missing := await unknown_styles(session, user_id, slugs):
            raise GenerationRequestError([f"unknown style @{s}" for s in missing])
        return inp

    def jobs(self, inp: CompareInput, reg: Registry) -> list[PlannedJob]:
        out = []
        for e in inp.entries:
            mode = self._mode(inp, reg, e)
            assert mode is not None
            roles = [m["role"] for m in self._media(inp, reg, e, mode)]
            # a saved style's pictures attach where the mode takes references (up to two, if there's room): priced
            # like pictures of yours, so a model that charges per input picture isn't quoted short
            spec, m = reg.models[e.model_id], reg.models[e.model_id].mode(mode)
            ref = (spec.elements or {}).get("ref_role", "image_references")
            slot = m.slot(ref) if m and (spec.elements or {}).get("strategy") == "refs_native" else None
            if slot and style_slug((take_direction(inp, e) or {}).get("style")):
                roles += [ref] * max(0, min(IMAGES_PER_KIND["style"], slot.max - roles.count(ref)))
            out.append(PlannedJob(e.model_id, mode, e.params, tuple(roles)))
        return out

    def plan(self, inp: CompareInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs = []
        keys = []
        for i, e in enumerate(inp.entries, 1):
            mode = self._mode(inp, reg, e)
            assert mode is not None
            key = take_key(i, e.model_id)
            keys.append(key)
            specs.append(StepSpec(key, StepKind.generate, self._label(reg, e), {
                "model_id": e.model_id, "mode": mode, "prompt": inp.prompt, "params": e.params,
                "media": self._media(inp, reg, e, mode), "direction": take_direction(inp, e),
            }, optional=True))  # one model failing shouldn't sink the comparison
        good = [(e, out) for k, e in zip(keys, inp.entries, strict=True) if (out := ok(settled, k))]
        if inp.pick and all(k in settled for k in keys) and len(good) > 1:
            specs.append(StepSpec("pick", StepKind.checkpoint, "Pick the best take", {
                "type": "pick_one",
                "options": [{"value": out["asset_id"], "label": self._label(reg, e)} for e, out in good],
            }, tuple(keys)))
        return specs

    def result(self, inp: CompareInput, settled: Settled) -> dict[str, Any] | None:
        keys = [take_key(i, e.model_id) for i, e in enumerate(inp.entries, 1)]
        if not all(k in settled for k in keys):
            return None
        takes = [{"model_id": e.model_id, "asset_id": (settled[k].get("asset_id")), "error": settled[k].get("failed")}
                 for k, e in zip(keys, inp.entries, strict=True)]
        good = [t for t in takes if t["asset_id"]]
        if not good:
            return {"takes": takes, "asset_id": None}
        if inp.pick and len(good) > 1:
            pick = ok(settled, "pick")
            if pick is None:
                return None
            return {"takes": takes, "asset_id": pick["choice"]}
        return {"takes": takes, "asset_id": good[0]["asset_id"]}
