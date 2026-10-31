from __future__ import annotations

from fastapi import APIRouter, HTTPException

from studio.api.schemas import PresetInputOut, PresetMoveOut, PresetOut, PresetRenderIn, PresetRenderOut
from studio.director.compiler import ShotDirection
from studio.director.library import FIELD_SECTIONS, get_library
from studio.domain.generations import GenerationRequestError
from studio.registry.loader import Registry, get_registry
from studio.registry.presets import PresetError, render
from studio.registry.pricing import estimate
from studio.registry.routing import local_only
from studio.registry.schema import ModelSpec, PresetSpec

router = APIRouter(prefix="/presets", tags=["presets"])


def _live_price(reg: Registry, spec: ModelSpec, preset: PresetSpec) -> float | None:
    params = {p.name: p.default for p in spec.params_for(preset.mode) if p.default is not None} | preset.params
    prices = []
    for route in spec.routes:
        provider = reg.providers.get(route.provider)
        if provider is None or provider.kind in ("mock", "local") or not route.enabled or preset.mode not in route.modes:
            continue
        try:
            prices.append(float(estimate(route.pricing, params, mode=preset.mode).usd))
        except (ValueError, KeyError):
            continue  # priced by input length etc.; unknown until the user picks media
    return min(prices) if prices else None


def _out(reg: Registry, preset: PresetSpec) -> PresetOut:
    spec = reg.models[preset.model_id]
    mode = spec.mode(preset.mode)
    assert mode is not None  # registry lint guarantees it
    library = get_library()
    direction = ShotDirection.model_validate(preset.direction or {})
    moves = [m for m in (library.move(c.id) for c in direction.camera_moves) if m]
    look = [item.label for name, section in FIELD_SECTIONS.items()
            if (item := library.look(section, getattr(direction, name)))]
    ar_param = spec.param("aspect_ratio")
    aspect = str(preset.params.get("aspect_ratio") or (ar_param.default if ar_param else None) or "16:9")
    local = local_only(reg, spec)  # a model that also runs on RunPod or a hosted API shows its live price
    return PresetOut(
        id=preset.id, name=preset.name, category=preset.category, summary=preset.summary, tags=preset.tags,
        model_id=spec.id, model_name=spec.name, output_type=spec.output_type, mode=mode.id, mode_label=mode.label,
        aspect_ratio=aspect,
        fields=preset.fields,
        inputs=[PresetInputOut(role=i.role, label=i.label or slot.label or i.role.replace("_", " "), slot=slot)
                for i in preset.inputs if (slot := mode.slot(i.role))],
        moves=[PresetMoveOut(id=m.id, label=m.label, category=m.category, glyph=m.glyph) for m in moves],
        look=look, local=local, live_price_usd=_live_price(reg, spec, preset),
    )


@router.get("", response_model=list[PresetOut])
async def list_presets() -> list[PresetOut]:
    """The effects and presets gallery."""
    reg = get_registry()
    order = ["camera", "effects", "look", "character", "product", "social", "edit"]
    presets = [p for p in reg.presets.values() if p.status == "active" and p.model_id in reg.models]
    presets.sort(key=lambda p: (order.index(p.category), p.name))
    return [_out(reg, p) for p in presets]


@router.post("/{preset_id}/render", response_model=PresetRenderOut,
             responses={422: {"description": "Missing answers or inputs"}})
async def render_preset(preset_id: str, body: PresetRenderIn) -> PresetRenderOut:
    """Fill a preset with the user's answers and media; returns the generation request it makes. Free."""
    preset = get_registry().presets.get(preset_id)
    if preset is None or preset.status != "active":
        raise HTTPException(404, f"unknown preset {preset_id}")
    media = [{"role": m.role, "asset_id": str(m.asset_id)} for m in body.media]
    try:
        out = render(preset, body.fields, media)
    except PresetError as exc:
        raise GenerationRequestError(exc.errors) from exc
    return PresetRenderOut.model_validate(out)
