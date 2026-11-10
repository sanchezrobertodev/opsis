"""Runway payload shapes that a flat field map can't express."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest


def act_two(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    """Act-Two takes `{type, uri}` objects: the character may be an image or a video, the reference a video."""
    kinds = {m.role: m.kind for m in req.media}
    if isinstance(payload.get("character"), str):
        payload["character"] = {"type": kinds.get("character_image") or "image", "uri": payload["character"]}
    if isinstance(payload.get("reference"), str):
        payload["reference"] = {"type": "video", "uri": payload["reference"]}
    return payload


# Kling 4.0's output sizes by resolution and aspect; "auto" and frame-led takes follow the model or the frame
_KLING_4_RATIOS = {
    "720p": {"16:9": "1280:720", "9:16": "720:1280", "1:1": "940:940", "21:9": "1460:624"},
    "1080p": {"16:9": "1920:1080", "9:16": "1080:1920", "1:1": "1440:1440", "21:9": "2200:940"},
}


def kling_4(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    """Kling 4.0: `ratio` names the size (aspect and resolution in one), and frames past the first go in one
    `promptImage` list of `{uri, position}` (the first, up to 8 middle ones, the last)."""
    res = str(req.params.get("resolution") or "720p")
    payload["ratio"] = _KLING_4_RATIOS.get(res, {}).get(str(req.params.get("aspect_ratio")), f"auto_{res}")
    middle, last = payload.pop("_middle", None) or [], payload.pop("_last", None)
    if "promptImage" in payload and (middle or last):
        middle = middle if isinstance(middle, list) else [middle]
        payload["promptImage"] = [{"uri": payload["promptImage"], "position": "first"},
                                  *({"uri": u, "position": "middle"} for u in middle),
                                  *([{"uri": last, "position": "last"}] if last else [])]
    return payload
