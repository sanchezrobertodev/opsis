"""LoRA payloads: a mentioned character's trained weights arrive as staged links in the `lora` (and, on two-expert
Wan 2.2 models, `lora_high`) roles; fal wants them as one `loras` list of {path, scale, transformer}."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest

LOW, HIGH = "_lora", "_lora_high"  # where the route's media map parks the staged links for this transform


def lora_list(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    """Pair each high-noise LoRA with its low-noise partner (same order, one per character) on Wan 2.2's two experts;
    a single-file LoRA (no `lora_high`) goes on both."""
    low, high = _links(payload.pop(LOW, None)), _links(payload.pop(HIGH, None))
    scale = float(req.params.get("lora_scale", 1.0))
    loras: list[dict[str, Any]] = []
    for i, path in enumerate(low):
        if i < len(high):
            loras += [{"path": high[i], "scale": scale, "transformer": "high"},
                      {"path": path, "scale": scale, "transformer": "low"}]
        else:
            loras.append({"path": path, "scale": scale, "transformer": "both"})
    if loras:
        payload["loras"] = loras
    return payload


def _links(value: Any) -> list[str]:
    if value is None:
        return []
    return [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str) and v]
