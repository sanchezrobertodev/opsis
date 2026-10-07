"""Object swap: replace or remove one object in a clip while the camera, light and motion stay as filmed.

The object is selected beforehand on one frame (SAM 3 on an image: clicks -> mask). Then:
  track (the mask through the clip) -> [mask checkpoint] -> edit[i:model] (each chosen edit model)
  -> keep[i:model] (composite the edit over the original through the matte) -> pick (if several takes)

Edit models are picked by what they do (the `object_replace` op on a clip), never by id. A model that takes a
mask gets the tracked matte directly; for the rest the matte only bounds the composite, which is what keeps
every pixel outside the object untouched. One model failing never sinks the comparison.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from studio.db.models import StepKind
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import mode_with_op
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec


class SwapModel(BaseModel):
    model_id: str
    params: dict[str, Any] = {}


class ObjectSwapInput(BaseModel):
    video_asset_id: uuid.UUID
    mask_asset_id: uuid.UUID = Field(description="The object's mask on one frame (from SAM 3 image)")
    frame_index: int = Field(0, ge=0, description="The frame the mask was drawn on")
    action: Literal["swap", "remove"] = "swap"
    prompt: str = Field("", max_length=2000, description="Swap: what it becomes. Remove: what it is (optional)")
    reference_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    models: list[SwapModel] = Field(min_length=1, max_length=3)
    track_model: str = "sam-3-video"
    keep_background: bool = Field(True, description="Composite through the matte so nothing else changes")
    feather: float = Field(4.0, ge=0, le=20, description="Softness of the composite's edge, in pixels")
    grow: int = Field(6, ge=0, le=12, description="How far the matte reaches past the object, in pixels")
    review_mask: bool = Field(False, description="Stop to check the tracked mask before editing")


def edit_key(i: int, model_id: str) -> str:
    return f"edit[{i}:{model_id}]"


def keep_key(i: int, model_id: str) -> str:
    return f"keep[{i}:{model_id}]"


def swap_mode(spec: ModelSpec) -> Mode | None:
    """A mode that changes objects in a clip: the `object_replace` op with a required input video."""
    for m in spec.modes:
        slot = m.slot("input_video")
        if "object_replace" in m.ops and slot and slot.required and m.prompt != "none":
            return m
    return None


def edit_prompt(inp: ObjectSwapInput) -> str:
    keep = "Keep the camera, lighting, motion and everything else exactly as filmed."
    if inp.action == "remove":
        what = inp.prompt.strip().rstrip(".") or "the selected object"
        return f"Remove {what} from the video and fill the space with the background that belongs there. {keep}"
    return f"{inp.prompt.strip().rstrip('.')}. Change only that object. {keep}"


class ObjectSwapRecipe(Recipe):
    id = "object_swap"
    name = "Object swap"
    summary = "Replace or remove one object while everything else stays as filmed."
    Input = ObjectSwapInput

    def title(self, inp: ObjectSwapInput) -> str:
        return f"{'Remove' if inp.action == 'remove' else 'Swap'}: {(inp.prompt or 'selected object')[:80]}"

    def validate(self, inp: ObjectSwapInput, reg: Registry) -> list[str]:
        errors = []
        track = reg.model(inp.track_model)
        if track is None or mode_with_op(track, "track") is None:
            errors.append(f"{inp.track_model!r} can't track an object through a clip")
        if inp.action == "swap" and not inp.prompt.strip():
            errors.append("say what the object should become")
        for entry in inp.models:
            spec = reg.model(entry.model_id)
            if spec is None:
                errors.append(f"unknown model {entry.model_id!r}")
            elif swap_mode(spec) is None:
                errors.append(f"{spec.name} can't replace objects in a clip")
        if len({e.model_id for e in inp.models}) != len(inp.models):
            errors.append("pick each model once")
        return errors

    def _media(self, inp: ObjectSwapInput, mode: Mode, matte: str | None) -> list[dict[str, str]]:
        media = [{"role": "input_video", "asset_id": str(inp.video_asset_id)}]
        if matte and mode.slot("mask"):
            media.append({"role": "mask", "asset_id": matte})
        refs = mode.slot("image_references")
        if refs:
            media += [{"role": "image_references", "asset_id": str(a)} for a in inp.reference_asset_ids[:refs.max]]
        return media

    def jobs(self, inp: ObjectSwapInput, reg: Registry) -> list[PlannedJob]:
        """The clip, mask and references exist already, so each take is priced on the real clip length."""
        track = reg.models[inp.track_model]
        out = [PlannedJob(track.id, mode_with_op(track, "track").id, {"frame_index": inp.frame_index},
                          ("input_video", "mask"), media=(("input_video", str(inp.video_asset_id)),
                                                          ("mask", str(inp.mask_asset_id))))]
        for entry in inp.models:
            mode = swap_mode(reg.models[entry.model_id])
            media = self._media(inp, mode, None)
            out.append(PlannedJob(entry.model_id, mode.id, entry.params,
                                  tuple(m["role"] for m in self._media(inp, mode, "planned")),
                                  media=tuple((m["role"], m["asset_id"]) for m in media)))
        return out

    def plan(self, inp: ObjectSwapInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        track_spec = reg.models[inp.track_model]
        specs = [StepSpec("track", StepKind.generate, "Track the object", {
            "model_id": track_spec.id, "mode": mode_with_op(track_spec, "track").id, "prompt": "",
            "params": {"frame_index": inp.frame_index},
            "media": [{"role": "input_video", "asset_id": str(inp.video_asset_id)},
                      {"role": "mask", "asset_id": str(inp.mask_asset_id)}]})]
        tracked = ok(settled, "track")
        matte = tracked["asset_id"] if tracked else None
        gate: tuple[str, ...] = ()
        if inp.review_mask:
            if tracked is None:
                return specs
            specs.append(StepSpec("mask", StepKind.checkpoint, "Check the tracked mask", {
                "type": "approve", "options": [{"value": "approve", "label": "The mask follows the object"}],
                "matte": matte}, ("track",)))
            if ok(settled, "mask") is None:
                return specs
            gate = ("mask",)

        keys = []
        for i, entry in enumerate(inp.models, 1):
            spec = reg.models[entry.model_id]
            mode = swap_mode(spec)
            assert mode is not None
            needs_matte = mode.slot("mask") is not None
            if needs_matte and matte is None:
                continue  # this model takes the matte itself; wait for tracking
            key = edit_key(i, spec.id)
            keys.append(key)
            specs.append(StepSpec(key, StepKind.generate, spec.name, {
                "model_id": spec.id, "mode": mode.id, "prompt": edit_prompt(inp), "params": entry.params,
                "media": self._media(inp, mode, matte), "direction": None,
            }, (*gate, *(("track",) if needs_matte else ())), optional=True))
            edited = ok(settled, key)
            if inp.keep_background and edited and matte:
                specs.append(StepSpec(keep_key(i, spec.id), StepKind.local, f"{spec.name}: keep the rest as filmed", {
                    "op": "keep_background", "original": str(inp.video_asset_id), "edited": edited["asset_id"],
                    "matte": matte, "feather": inp.feather, "grow": inp.grow, "title": self.title(inp),
                }, (key, "track"), optional=True))  # one take failing to composite shouldn't sink the others

        takes = self._takes(inp, settled)
        good = [t for t in takes if t["asset_id"]]
        if takes and all(t["done"] for t in takes) and len(good) > 1:
            specs.append(StepSpec("pick", StepKind.checkpoint, "Pick the best take", {
                "type": "pick_one", "options": [{"value": t["asset_id"], "label": reg.models[t["model_id"]].name}
                                                for t in good]},
                tuple(s.key for s in specs if s.key.startswith(("edit[", "keep[")))))
        return specs

    def _takes(self, inp: ObjectSwapInput, settled: Settled) -> list[dict[str, Any]]:
        """Each model's take: the composite when the background is kept, else the raw edit."""
        out = []
        for i, entry in enumerate(inp.models, 1):
            key = edit_key(i, entry.model_id)
            edited = settled.get(key)
            take = {"model_id": entry.model_id, "asset_id": None, "raw_asset_id": None, "error": None, "done": False}
            if edited is None or "track" not in settled:
                out.append(take)
                continue
            if "failed" in edited:
                take.update(error=edited["failed"], done=True)
            elif inp.keep_background:
                kept = settled.get(keep_key(i, entry.model_id))
                if kept is not None:
                    take.update(asset_id=kept.get("asset_id"), raw_asset_id=edited["asset_id"],
                                error=kept.get("failed"), done=True)
            else:
                take.update(asset_id=edited["asset_id"], raw_asset_id=edited["asset_id"], done=True)
            out.append(take)
        return out

    def failure(self, inp: ObjectSwapInput, settled: Settled) -> str | None:
        takes = self._takes(inp, settled)
        if takes and all(t["done"] for t in takes) and not any(t["asset_id"] for t in takes):
            reasons = "; ".join(f"{t['model_id']}: {t['error']}" for t in takes if t["error"])
            return f"No model produced a take. {reasons}"
        return None

    def result(self, inp: ObjectSwapInput, settled: Settled) -> dict[str, Any] | None:
        takes = self._takes(inp, settled)
        if not all(t["done"] for t in takes):
            return None
        good = [t for t in takes if t["asset_id"]]
        matte = (ok(settled, "track") or {}).get("asset_id")
        clean = [{k: v for k, v in t.items() if k != "done"} for t in takes]
        if len(good) > 1:
            pick = ok(settled, "pick")
            if pick is None:
                return None
            return {"asset_id": pick["choice"], "takes": clean, "matte_asset_id": matte}
        return {"asset_id": good[0]["asset_id"] if good else None, "takes": clean, "matte_asset_id": matte}
