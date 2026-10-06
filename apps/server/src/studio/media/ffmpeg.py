"""ffmpeg/ffprobe helpers.

Windows rule: never use asyncio subprocesses (the worker runs a SelectorEventLoop, which can't spawn
them). Every call goes through subprocess.run on a worker thread via anyio.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class FFmpegError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-1500:]
        raise FFmpegError(f"{Path(args[0]).name} exited {proc.returncode}: {tail}")
    return proc


async def run(args: list[str], cwd: Path | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    return await anyio.to_thread.run_sync(lambda: _run(args, cwd, timeout))


async def ffmpeg(*args: str, cwd: Path | None = None, timeout: float = 600) -> None:
    await run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd, timeout=timeout)


def font_file_arg() -> str | None:
    """drawtext fontfile value with the colon escaped, or None to let fontconfig decide."""
    if sys.platform == "win32":
        for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
            if Path("C:/Windows/Fonts", name).exists():
                return f"C\\:/Windows/Fonts/{name}"
        return None
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if Path(cand).exists():
            return cand
    return None


@dataclass
class Probe:
    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    has_audio: bool
    codec: str | None
    format_name: str | None
    raw: dict[str, Any]


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    num, _, den = rate.partition("/")
    try:
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return None


async def probe(path: Path) -> Probe:
    proc = await run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60
    )
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = fmt.get("duration") or (video or audio or {}).get("duration")
    return Probe(
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration_ms=int(float(duration) * 1000) if duration not in (None, "N/A") else None,
        fps=_fps(video.get("avg_frame_rate")) if video else None,
        has_audio=audio is not None,
        codec=(video or audio or {}).get("codec_name"),
        format_name=fmt.get("format_name"),
        raw={"format": fmt, "streams": streams},
    )
