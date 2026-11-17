"""LoRA payloads: a mentioned character's trained weights arrive as staged links in the `lora` (and, on two-expert
Wan 2.2 models, `lora_high`) roles; fal wants them as one `loras` list of {path, scale}, plus the `transformer` each
goes on where the model has two experts."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest

LOW, HIGH = "_lora", "_lora_high"  # where the route's media map parks the staged links for this transform


def lora_list(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    """On a mode with a `lora_high` slot (Wan 2.2's two experts), pair each high-noise LoRA with its low-noise partner;
    a single-file LoRA goes on both. Pairing is by position: the elements resolver lists two-expert characters first,
    in the same order in both roles, and single files after them. A one-transformer model (FLUX 3 Klein) takes each
    file as plain {path, scale}."""
    low, high = _links(payload.pop(LOW, None)), _links(payload.pop(HIGH, None))
    scale = float(req.params.get("lora_scale", 1.0))
    mode = spec.mode(req.mode)
    loras: list[dict[str, Any]] = []
    if mode is None or mode.slot("lora_high") is None:
        loras = [{"path": path, "scale": scale} for path in low]
    else:
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
