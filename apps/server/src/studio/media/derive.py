"""Derivatives for the library: poster, thumbnail, hover-scrub filmstrip, browser-safe proxy."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from studio.media.ffmpeg import Probe, ffmpeg

THUMB_WIDTH = 480
FILMSTRIP_FRAMES = 10
FILMSTRIP_FRAME_WIDTH = 240
BROWSER_SAFE_VIDEO = {"h264", "vp9", "av1"}


async def video_poster(src: Path, dest: Path, duration_ms: int | None) -> None:
    at = max(0.0, min(1.0, (duration_ms or 0) / 1000 * 0.1))
    await ffmpeg("-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(dest))


def image_thumb(src: Path, dest: Path, width: int = THUMB_WIDTH) -> None:
    with Image.open(src) as im:
        im = im.convert("RGB")
        ratio = width / im.width
        im = im.resize((width, max(1, round(im.height * ratio))), Image.Resampling.LANCZOS)
        im.save(dest, "WEBP", quality=82)


async def video_filmstrip(src: Path, dest: Path, probe: Probe) -> int:
    """Horizontal sprite of evenly spaced frames; the UI maps cursor x to a frame. Returns frame count."""
    duration = (probe.duration_ms or 1000) / 1000
    frames = FILMSTRIP_FRAMES
    fps = frames / max(duration, 0.1)
    await ffmpeg(
        "-i", str(src),
        "-vf", f"fps={fps:.4f},scale={FILMSTRIP_FRAME_WIDTH}:-2,tile={frames}x1",
        "-frames:v", "1", "-q:v", "4", str(dest),
    )
    return frames


async def video_proxy(src: Path, dest: Path) -> None:
    """H.264/AAC MP4 for codecs browsers can't play (e.g. ProRes, HEVC from some providers)."""
    await ffmpeg(
        "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    )


def needs_proxy(probe: Probe, mime: str) -> bool:
    return mime.startswith("video/") and (probe.codec or "") not in BROWSER_SAFE_VIDEO
