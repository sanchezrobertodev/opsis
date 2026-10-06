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
