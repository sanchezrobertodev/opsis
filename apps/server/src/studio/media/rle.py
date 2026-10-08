"""Run-length masks to a matte video.

fal's SAM 3 tracker (`sam-3/video-rle`) answers with one string per frame: space-separated "start length" pairs over
the frame's pixels in row-major order (checked live 2026-10-08: a 1280x720 frame's first run starts on the row where
the reported box begins). A route whose output names this decoder gets a black-and-white H.264 clip (white = the
object) at the input clip's size and frame rate, one frame per mask; a missing or empty mask is a black frame.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import anyio

from studio.media.ffmpeg import FFMPEG
from studio.registry.validate import MediaRef

DEFAULT_FPS = 24.0
WHITE = bytes([255])


def decode_frame(rle: str | None, width: int, height: int) -> bytes:
    """One mask as width x height gray pixels, row by row: 255 on the object, 0 elsewhere."""
    size = width * height
    flat = bytearray(size)
    numbers = [int(n) for n in (rle or "").split()]
    for start, length in zip(numbers[0::2], numbers[1::2], strict=False):
        lo, hi = max(0, start), min(size, start + length)
        if hi > lo:
            flat[lo:hi] = WHITE * (hi - lo)
    return bytes(flat)


def _size_and_rate(media: list[MediaRef]) -> tuple[int, int, float]:
    clip = next((m for m in media if m.role == "input_video"), None)
    if clip is None or not clip.width or not clip.height:
        raise ValueError("the clip's size is unknown, so its masks can't be drawn")
    return clip.width, clip.height, float(clip.fps or DEFAULT_FPS)


def _encode(frames: list[str | None], width: int, height: int, fps: float, dest: Path, workdir: Path) -> None:
    raw = workdir / "masks.gray"
    with raw.open("wb") as fh:  # one frame at a time, so a long clip never sits in memory whole
        for rle in frames:
            fh.write(decode_frame(rle, width, height))
    # yuv420p needs even sizes: an odd clip gets a black pixel row or column, which never matters to a matte
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
           "-r", f"{fps:g}", "-i", str(raw), "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(dest)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    raw.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg couldn't write the matte: {result.stderr.strip()[-400:]}")


async def mask_video(value: Any, *, media: list[MediaRef], dest: Path, workdir: Path) -> str:
    """Decoder hook (OutputSpec.decode): the RLE list at the route's output path becomes a matte clip at `dest`."""
    frames = value if isinstance(value, list) else [value]
    if not any(isinstance(f, str) and f.strip() for f in frames):
        raise ValueError("the tracker found the object in no frame")
    width, height, fps = _size_and_rate(media)
    await anyio.to_thread.run_sync(_encode, frames, width, height, fps, dest, workdir)
    return "video/mp4"
