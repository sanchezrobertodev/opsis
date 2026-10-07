"""Segmentation payloads: click points arrive as [x, y, label] in 0-1 of the image; SAM wants pixels."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest


def sam_points(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    points = payload.pop("point_prompts", None) or []
    image = next((m for m in req.media if m.role == "input_image"), None)
    if points:
        if image is None or not image.width or not image.height:
            raise ValueError("the image's size is unknown, so clicks can't be placed")
        w, h = image.width, image.height
        payload["point_prompts"] = [{"x": round(x * (w - 1)), "y": round(y * (h - 1)), "label": int(label)}
                                    for x, y, label in points]
    payload.setdefault("prompt", "")  # fal's default prompt is "wheel"; clicks alone need an empty one
    return payload
