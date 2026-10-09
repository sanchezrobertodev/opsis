"""Songs with lyrics: a `sections` param ([{name, lines, duration}]) parked by the route's map as `_song`, sent as
ElevenLabs' composition plan for music_v1 (sections with their lines and durations, which the model keeps), with the
prompt as the global style. A plan can't go with a prompt or a length, so those leave the payload."""

from __future__ import annotations

import re
from typing import Any

from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest

SONG = "_song"


def song_plan(*, spec: ModelSpec, route: Route, req: NormalizedRequest, payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload.pop(SONG, None)
    if not sections:
        return payload
    styles = [s.strip() for s in re.split(r"[,;]\s*", str(payload.pop("prompt", "") or "")) if s.strip()]
    payload.pop("music_length_ms", None)
    payload.pop("force_instrumental", None)
    payload["composition_plan"] = {
        "positive_global_styles": styles[:12],
        "negative_global_styles": [],
        "sections": [{"section_name": s["name"], "positive_local_styles": [], "negative_local_styles": [],
                      "duration_ms": round(s["duration"] * 1000), "lines": s["lines"]} for s in sections],
    }
    payload["respect_sections_durations"] = True  # the captions follow the sections' timing
    return payload
