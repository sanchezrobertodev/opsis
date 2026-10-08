"""Run-length masks to a matte video.

fal's SAM 3 tracker (`sam-3/video-rle`) answers with one string per frame: space-separated "start length" pairs over
the frame's pixels in row-major order (checked live 2026-10-08: a 1280x720 frame's first run starts on the row where
the reported box begins). It may also answer with a file holding them (its documented SAM3RLEFileOutput), which is
downloaded first. A route whose output names this decoder gets a black-and-white H.264 clip (white = the object) at
the input clip's size and frame rate, one frame per mask; a missing or empty mask is a black frame.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import anyio

from studio.media.ffmpeg import FFMPEG
from studio.providers.common import download_url
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


def parse_masks(text: str) -> list[Any]:
    """The masks in a downloaded RLE file: a JSON list, a JSON object holding `rle`, or one mask per line."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.splitlines()
    if isinstance(data, dict):
        data = data.get("rle", [])
    return data if isinstance(data, list) else [data]


async def _masks(value: Any, workdir: Path) -> list[Any]:
    """The per-frame masks, whether fal put them in its answer or in a file it links to."""
    if isinstance(value, dict) and value.get("url"):
        path = workdir / "masks.rle"
        await download_url(value["url"], path)
        return parse_masks(await anyio.Path(path).read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]


def _size_and_rate(media: list[MediaRef]) -> tuple[int, int, float]:
    clip = next((m for m in media if m.role == "input_video"), None)
    if clip is None or not clip.width or not clip.height:
        raise ValueError("the clip's size is unknown, so its masks can't be drawn")
    return clip.width, clip.height, float(clip.fps or DEFAULT_FPS)


def _encode(frames: list[Any], width: int, height: int, fps: float, dest: Path, workdir: Path) -> None:
    """Feed the frames to ffmpeg one at a time (nothing uncompressed is kept, however long or large the clip). Runs
    in a worker thread: a plain subprocess, never asyncio's (Windows rule in CLAUDE.md)."""
    # yuv420p needs even sizes: an odd clip gets a black pixel row or column, which never matters to a matte
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
           "-r", f"{fps:g}", "-i", "-", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(dest)]
    log = workdir / "ffmpeg.log"
    with log.open("w") as err:  # to a file, so a chatty ffmpeg can never block on a full pipe while we write
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        assert proc.stdin is not None
        try:
            for rle in frames:
                proc.stdin.write(decode_frame(rle if isinstance(rle, str) else None, width, height))
        except BrokenPipeError:
            pass  # ffmpeg stopped early; its log says why
        finally:
            proc.stdin.close()
            code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg couldn't write the matte: {log.read_text(errors='replace').strip()[-400:]}")


async def mask_video(value: Any, *, media: list[MediaRef], dest: Path, workdir: Path) -> str:
    """Decoder hook (OutputSpec.decode): the RLE masks at the route's output path become a matte clip at `dest`."""
    frames = await _masks(value, workdir)
    if not any(isinstance(f, str) and f.strip() for f in frames):
        raise ValueError("the tracker found the object in no frame")
    width, height, fps = _size_and_rate(media)
    await anyio.to_thread.run_sync(_encode, frames, width, height, fps, dest, workdir)
    return "video/mp4"
