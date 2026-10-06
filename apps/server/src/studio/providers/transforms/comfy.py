"""Payload hooks for ComfyUI routes: turn canonical quality/aspect params into the pixel sizes each
validated workflow was tuned for. Registered on a route with `transform: studio.providers.transforms.comfy:<fn>`.
"""

from __future__ import annotations

from typing import Any

# Wan 2.2 TI2V 5B sizes from the VideoStudio benchmarks (RTX 5070, 12 GB).
WAN_SIZES = {
    ("draft", "16:9"): (768, 448),
    ("draft", "9:16"): (448, 768),
    ("standard", "16:9"): (1280, 704),
    ("standard", "9:16"): (704, 1280),
}

# FLUX.2 sizes before the 1.5x upscale, multiples of 16 near one megapixel.
FLUX_SIZES = {
    "1:1": (1024, 1024),
    "16:9": (1216, 688),
    "9:16": (688, 1216),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
}


def wan_size(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    quality = str(req.params.get("quality", "draft"))
    aspect = str(req.params.get("aspect_ratio", "16:9"))
    payload["width"], payload["height"] = WAN_SIZES.get((quality, aspect), WAN_SIZES[("draft", "16:9")])
    return payload


def flux_size(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    payload["width"], payload["height"] = FLUX_SIZES.get(str(req.params.get("aspect_ratio", "1:1")), FLUX_SIZES["1:1"])
    return payload


# Wan 2.2 14B on the wan-core RunPod endpoint: 480p and 720p buckets (multiples of 16).
WAN14B_SIZES = {
    ("480p", "16:9"): (832, 480),
    ("480p", "9:16"): (480, 832),
    ("480p", "1:1"): (624, 624),
    ("720p", "16:9"): (1280, 720),
    ("720p", "9:16"): (720, 1280),
    ("720p", "1:1"): (960, 960),
}


def wan14b_size(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    quality = str(req.params.get("quality", "720p"))
    aspect = str(req.params.get("aspect_ratio", "16:9"))
    payload["width"], payload["height"] = WAN14B_SIZES.get((quality, aspect), WAN14B_SIZES[("720p", "16:9")])
    return payload
