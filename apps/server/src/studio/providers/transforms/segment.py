"""Segmentation payloads: click points arrive as [x, y, label] in 0-1 of the picture (or of the clip's frame, when
tracking); SAM wants pixels."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest


def sam_points(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    points = payload.pop("point_prompts", None) or []
    image = next((m for m in req.media if m.role == "input_image"), None) \
        or next((m for m in req.media if m.role == "input_video"), None)
    if points:
        if image is None or not image.width or not image.height:
            raise ValueError("the picture's size is unknown, so clicks can't be placed")
        w, h = image.width, image.height
        # on a clip each click also names its frame (fal asks for it; seen live 2026-10-07)
        frame = {"frame_index": int(payload.get("frame_index") or 0)} if image.role == "input_video" else {}
        payload["point_prompts"] = [{"x": round(x * (w - 1)), "y": round(y * (h - 1)), "label": int(label), **frame}
                                    for x, y, label in points]
    payload.setdefault("prompt", "")  # fal's default prompt is "wheel"; clicks alone need an empty one
    return payload
