"""Cost estimation from a route's `pricing` block.

Pure functions: given a normalized request (params already defaulted), return a USD estimate plus the
basis used, so the number shown on the Generate button can be explained and re-checked later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from simpleeval import EvalWithCompoundTypes

from studio.registry.schema import Multiplier, Pricing, Rate, Scalar

_SHORT_SIDE = {"360p": 360, "480p": 480, "512p": 512, "540p": 540, "720p": 720, "768p": 768, "1080p": 1080,
               "1440p": 1440, "4k": 2160}
_LONG_SIDE_IMAGE = {"512": 512, "1k": 1024, "1.5k": 1536, "2k": 2048, "4k": 4096}


@dataclass
class CostEstimate:
    usd: Decimal
    basis: str
    quantity: float
    rate_usd: float
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"usd": float(self.usd), "basis": self.basis, "quantity": self.quantity,
                "rate_usd": self.rate_usd, **self.detail}


def dims_for(resolution: str | None, aspect: str | None) -> tuple[int, int]:
    """Approximate output pixel dimensions from a resolution label and aspect ratio."""
    res = (resolution or "720p").lower()
    try:
        aw, ah = (float(x) for x in (aspect or "16:9").split(":"))
    except ValueError:
        aw, ah = 16.0, 9.0
    if res in _LONG_SIDE_IMAGE:
        long_side = _LONG_SIDE_IMAGE[res]
        if aw >= ah:
            return long_side, round(long_side * ah / aw)
        return round(long_side * aw / ah), long_side
    short = _SHORT_SIDE.get(res, 720)
    if aw >= ah:
        return round(short * aw / ah), short
    return short, round(short * ah / aw)


def _matches(when: dict[str, Scalar | list[Scalar]], values: dict[str, Any]) -> bool:
    for key, expected in when.items():
        actual = values.get(key)
        options = expected if isinstance(expected, list) else [expected]
        if actual not in options and str(actual) not in {str(o) for o in options}:
            return False
    return True


def media_variables(media: list[Any] | None) -> dict[str, float]:
    """Totals over input media, for routes that bill by input (video edits bill by input length)."""
    out = {"input_video_seconds": 0.0, "input_audio_seconds": 0.0, "input_images": 0.0}
    for m in media or []:
        kind = getattr(m, "kind", None) or (m.get("kind") if isinstance(m, dict) else None)
        ms = getattr(m, "duration_ms", None) if not isinstance(m, dict) else m.get("duration_ms")
        if kind == "video":
            out["input_video_seconds"] += (ms or 0) / 1000
        elif kind == "audio":
            out["input_audio_seconds"] += (ms or 0) / 1000
        elif kind == "image":
            out["input_images"] += 1
    return out


def pricing_variables(params: dict[str, Any], prompt: str = "", num_outputs: int | None = None,
                      media: list[Any] | None = None) -> dict[str, Any]:
    width, height = dims_for(params.get("resolution"), params.get("aspect_ratio"))
    duration = float(params.get("duration") or 0)
    n = num_outputs or int(params.get("num_outputs") or params.get("num_images") or 1)
    return {
        **params,
        "duration": duration,
        "num_outputs": n,
        "width": width,
        "height": height,
        "fps": float(params.get("fps") or 24),
        "megapixels": width * height / 1_000_000,
        "prompt_chars": len(prompt or ""),
        **media_variables(media),
    }


def _pick_rate(rates: list[Rate], values: dict[str, Any]) -> Rate:
    specific = [r for r in rates if r.when and _matches(r.when, values)]
    if specific:
        return max(specific, key=lambda r: len(r.when))  # most specific match wins
    fallback = [r for r in rates if not r.when]
    if fallback:
        return fallback[0]
    raise ValueError(f"no pricing rate matches {values!r}")


def _apply_multipliers(multipliers: list[Multiplier], values: dict[str, Any]) -> float:
    factor = 1.0
    for m in multipliers:
        if _matches(m.when, values):
            factor *= m.factor
    return factor


def _evaluate(expr: str, names: dict[str, Any]) -> float:
    return float(EvalWithCompoundTypes(names=names, functions={"max": max, "min": min, "round": round}).eval(expr))


def estimate(pricing: Pricing, params: dict[str, Any], prompt: str = "",
             media: list[Any] | None = None) -> CostEstimate:
    values = pricing_variables(params, prompt, media=media)

    if pricing.basis == "formula":
        usd = _evaluate(pricing.formula or "0", values)
        return _finish(usd, pricing, "formula", 1.0, usd, values)

    if pricing.quantity:
        quantity = _evaluate(pricing.quantity, values)
    elif pricing.basis == "per_output_second":
        quantity = values["duration"] * values["num_outputs"]
    elif pricing.basis == "per_image":
        quantity = float(values["num_outputs"])
    elif pricing.basis == "per_1k_chars":
        quantity = values["prompt_chars"] / 1000
    elif pricing.basis == "per_video_token":
        quantity = values["width"] * values["height"] * values["fps"] * values["duration"] / 1024
    else:  # per_request, per_gpu_second without an explicit quantity
        quantity = 1.0

    rate = _pick_rate(pricing.rates, values)
    usd = rate.usd * quantity / rate.per * _apply_multipliers(pricing.multipliers, values)
    return _finish(usd, pricing, pricing.basis, quantity, rate.usd, values)


def _finish(usd: float, pricing: Pricing, basis: str, quantity: float, rate: float, values: dict) -> CostEstimate:
    usd = max(usd, pricing.minimum_usd, 0.0)
    amount = Decimal(str(usd)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return CostEstimate(
        usd=amount,
        basis=basis,
        quantity=round(quantity, 4),
        rate_usd=rate,
        detail={"verified": pricing.verified.isoformat() if pricing.verified else None},
    )


def eta_seconds(expression: str | None, params: dict[str, Any]) -> float | None:
    """Expected run time for a route (local GPUs especially), from the route's `eta` expression."""
    if not expression:
        return None
    try:
        return max(0.0, _evaluate(expression, pricing_variables(params)))
    except Exception:
        return None
