"""Payload hooks for ComfyUI routes: turn canonical quality/aspect params into the pixel sizes each
validated workflow was tuned for. Registered on a route with `transform: studio.providers.transforms.comfy:<fn>`.
"""

from __future__ import annotations

import json
import math
from itertools import pairwise
from typing import Any

# Wan 2.2 TI2V 5B sizes benchmarked on a 12 GB GPU.
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


# LTX-2.5 on the wan-core endpoint. Its latents are 1/32 of the picture, and the two-stage graphs sample at half size
# before the x2 upscaler, so every size is a multiple of 64 (the half a multiple of 32), near the template's 0.9 MP.
LTX_SIZES = {
    ("720p", "16:9"): (1280, 704),
    ("720p", "9:16"): (704, 1280),
    ("720p", "1:1"): (960, 960),
    ("1080p", "16:9"): (1920, 1088),
    ("1080p", "9:16"): (1088, 1920),
    ("1080p", "1:1"): (1408, 1408),
}


def ltx_size(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Full and half sizes for the LTX-2.5 graphs: the two-stage ones (text, start frame) start at half size; the
    first-and-last-frame one runs in one pass at full size. Frames are scaled and center-cropped to the full size."""
    resolution = str(req.params.get("resolution", "720p"))
    aspect = str(req.params.get("aspect_ratio", "16:9"))
    width, height = LTX_SIZES.get((resolution, aspect), LTX_SIZES[("720p", "16:9")])
    two_stage = "end_image" not in payload
    if two_stage:
        payload["half_width"], payload["half_height"] = width // 2, height // 2
    if "start_image" in payload or not two_stage:
        payload["width"], payload["height"] = width, height
    return payload


# Wan-Move runs at 480p. The workflow scales and center-crops the start frame to this size, which is the frame the
# motion-path editor draws on (cropped to the same aspect), so 0-1 path coordinates map straight to pixels.
WAN_MOVE_SIZES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (624, 624)}
# Like ComfyUI's GenerateTracks: each stroke becomes a few parallel tracks spread across it, so a region moves
# rather than a single pixel.
TRACKS_PER_PATH = 5
TRACK_SPREAD = 0.015  # fraction of the average side between neighbouring tracks
# Wan-Move copies the start frame's latent cell (8 x 8 px) under each track to where the track is later, so a pin's 5
# still tracks hold a thin strip of cells. Live takes (2026-10-08) showed that is enough: the pinned spot
# changed no more than untouched background. Don't pack a pin with a disc of still tracks: Wan-Move drew that patch on
# as a visible ring.


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


# Clip-driven wan-core workflows (VACE edits, Animate-2) keep the clip's own aspect at the quality's pixel budget. The
# route re-times the clip to Wan's 16 fps (`media_fps`), so up to 81 frames cover 5 s of it, whatever its frame rate.
CLIP_PIXELS = {"480p": 832 * 480, "720p": 1280 * 720}
MAX_CLIP_FRAMES = 81


def wan_clip_size(*, spec: Any, route: Any, req: Any, payload: dict[str, Any]) -> dict[str, Any]:
    clip = next((m for m in req.media if m.role in ("input_video", "driving_video")), None)
    w, h = (clip.width, clip.height) if clip is not None and clip.width and clip.height else (16, 9)
    scale = (CLIP_PIXELS.get(str(req.params.get("quality", "480p")), CLIP_PIXELS["480p"]) / (w * h)) ** 0.5
    payload["width"], payload["height"] = max(16, int(w * scale) // 16 * 16), max(16, int(h * scale) // 16 * 16)
    fps = (route.media_fps.get(clip.role) if clip is not None and route is not None else None) or (clip.fps if clip else None)
    frames = round(clip.duration_ms / 1000 * (fps or 16)) + 1 if clip is not None and clip.duration_ms else MAX_CLIP_FRAMES
    n = min(max(frames, 5), MAX_CLIP_FRAMES)
    payload["length"] = (n - 1) // 4 * 4 + 1  # Wan wants 4k + 1 frames
    return payload
