"""Compare models: one prompt on several models side by side, then pick the take you like."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_for
from studio.registry.loader import Registry


class CompareEntry(BaseModel):
    model_id: str
    mode: str | None = Field(None, description="Defaults to the mode that fits the media given")
    params: dict[str, Any] = {}


class CompareInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    entries: list[CompareEntry] = Field(min_length=2, max_length=4)
    media: list[dict[str, str]] = Field(default_factory=list, description="[{role, asset_id}], shared by every model")
    direction: dict[str, Any] | None = None
    pick: bool = Field(True, description="Ask which take is best once they are all done")


def take_key(i: int, model_id: str) -> str:
    return f"take[{i}:{model_id}]"


class CompareRecipe(Recipe):
    id = "compare"
    name = "Compare models"
    summary = "The same prompt on several models at once, side by side."
    Input = CompareInput

    def title(self, inp: CompareInput) -> str:
        return f"Compare: {inp.prompt[:80]}"

    def _mode(self, inp: CompareInput, reg: Registry, entry: CompareEntry) -> str | None:
        spec = reg.model(entry.model_id)
        if spec is None:
            return None
        if entry.mode:
            return entry.mode if spec.mode(entry.mode) else None
        m = mode_for(spec, {x["role"] for x in inp.media})
        return m.id if m else None

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
        if len(types) > 1:
            errors.append("compare models that make the same kind of output (all video or all image)")
        return errors

    def jobs(self, inp: CompareInput, reg: Registry) -> list[PlannedJob]:
        out = []
        for e in inp.entries:
            mode = self._mode(inp, reg, e)
            assert mode is not None
            out.append(PlannedJob(e.model_id, mode, e.params, tuple(m["role"] for m in self._media(inp, reg, e, mode))))
        return out

    def plan(self, inp: CompareInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        specs = []
        keys = []
        for i, e in enumerate(inp.entries, 1):
            mode = self._mode(inp, reg, e)
            assert mode is not None
            key = take_key(i, e.model_id)
            keys.append(key)
            specs.append(StepSpec(key, StepKind.generate, reg.models[e.model_id].name, {
                "model_id": e.model_id, "mode": mode, "prompt": inp.prompt, "params": e.params,
                "media": self._media(inp, reg, e, mode), "direction": inp.direction,
            }, optional=True))  # one model failing shouldn't sink the comparison
        good = [(e, out) for k, e in zip(keys, inp.entries, strict=True) if (out := ok(settled, k))]
        if inp.pick and all(k in settled for k in keys) and len(good) > 1:
            specs.append(StepSpec("pick", StepKind.checkpoint, "Pick the best take", {
                "type": "pick_one",
                "options": [{"value": out["asset_id"], "label": reg.models[e.model_id].name} for e, out in good],
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
