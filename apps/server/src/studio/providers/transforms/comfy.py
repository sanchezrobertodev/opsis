"""Payload hooks for ComfyUI routes: turn canonical quality/aspect params into the pixel sizes each
validated workflow was tuned for. Registered on a route with `transform: studio.providers.transforms.comfy:<fn>`.
"""

from __future__ import annotations

import json
import math
from itertools import pairwise
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


# Wan-Move runs at 480p. The workflow scales and center-crops the start frame to this size, which is the frame the
# motion-path editor draws on (cropped to the same aspect), so 0-1 path coordinates map straight to pixels.
WAN_MOVE_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (624, 624)}
# Like ComfyUI's GenerateTracks: each stroke becomes a few parallel tracks spread across it, so a region moves
# rather than a single pixel.
TRACKS_PER_PATH = 5
TRACK_SPREAD = 0.015  # fraction of the average side between neighbouring tracks


def wan_move_tracks(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    width, height = WAN_MOVE_SIZES.get(str(req.params.get("aspect_ratio", "16:9")), WAN_MOVE_SIZES["16:9"])
    payload["width"], payload["height"] = width, height
    frames = int(payload.get("length") or 81)
    tracks = paths_to_tracks(req.params.get("motion_paths") or [], width, height, frames)
    payload["track_coords"] = json.dumps(tracks, separators=(",", ":"))
    return payload


def paths_to_tracks(paths: list[dict[str, Any]], width: int, height: int, frames: int) -> list[list[dict[str, float]]]:
    """Drawn paths (0-1 points) -> per-frame pixel tracks. A stroke is followed at constant speed over the whole
    clip; a single point holds still for the whole clip."""
    spread = TRACK_SPREAD * (width + height) / 2
    offsets = [(i - (TRACKS_PER_PATH - 1) / 2) * spread for i in range(TRACKS_PER_PATH)]
    out: list[list[dict[str, float]]] = []
    for path in paths:
        pts = [(float(x) * width, float(y) * height) for x, y in path["points"]]
        centre = _resample(pts, frames)
        for off in offsets:
            track = []
            for i, (x, y) in enumerate(centre):
                if len(pts) == 1:
                    nx, ny = 1.0, 0.0  # a still point: spread its tracks sideways
                else:
                    ax, ay = centre[max(i - 1, 0)]
                    bx, by = centre[min(i + 1, frames - 1)]
                    dx, dy = bx - ax, by - ay
                    norm = math.hypot(dx, dy)
                    nx, ny = (-dy / norm, dx / norm) if norm else (1.0, 0.0)
                track.append({"x": round(min(max(x + nx * off, 0.0), width - 1), 1),
                              "y": round(min(max(y + ny * off, 0.0), height - 1), 1)})
            out.append(track)
    return out


def _resample(pts: list[tuple[float, float]], frames: int) -> list[tuple[float, float]]:
    """`frames` positions spaced evenly along the polyline (by distance travelled)."""
    if len(pts) == 1:
        return [pts[0]] * frames
    cumulative = [0.0]
    for (ax, ay), (bx, by) in pairwise(pts):
        cumulative.append(cumulative[-1] + math.hypot(bx - ax, by - ay))
    total = cumulative[-1]
    if total == 0:
        return [pts[0]] * frames
    out, seg = [], 0
    for i in range(frames):
        target = total * i / (frames - 1) if frames > 1 else 0.0
        while seg < len(pts) - 2 and cumulative[seg + 1] < target:
            seg += 1
        span = cumulative[seg + 1] - cumulative[seg]
        t = (target - cumulative[seg]) / span if span else 0.0
        (ax, ay), (bx, by) = pts[seg], pts[seg + 1]
        out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return out
