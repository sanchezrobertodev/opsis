"""Marble world prompts: several pictures of one place arrive as staged content objects parked in `_views`; World Labs
wants them as `world_prompt.multi_image_prompt`, a list of {content, azimuth?}. When the pictures were taken turning
around the place, in order, each faces an even share of the circle (the first straight ahead)."""

from __future__ import annotations

from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest

VIEWS = "_views"  # where the route's media map parks the staged pictures for this transform


def marble_views(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    views = payload.pop(VIEWS, None)
    if views is None:
        return payload
    views = views if isinstance(views, list) else [views]
    around = req.params.get("views_facing") == "around"
    step = 360 / len(views) if views else 0
    prompt = payload.setdefault("world_prompt", {})
    prompt["multi_image_prompt"] = [
        {"content": v, **({"azimuth": round(i * step, 2)} if around else {})} for i, v in enumerate(views)
    ]
    return payload
