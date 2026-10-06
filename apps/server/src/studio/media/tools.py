"""Local video tools: reframe, trim, retime, reverse, loop, smooth motion, upscale, stabilize. Pure ffmpeg.

Every function reads `src` and writes an H.264/AAC MP4 at `dest`. Audio is kept where the operation keeps
time intact, re-timed where it changes speed, and dropped only where it can't be made to fit (reversing a
clip reverses its sound too).
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from studio.media.ffmpeg import FFMPEG, FFmpegError, Probe, ffmpeg, probe
from studio.media.ops import encode_args

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "4:3": (4, 3), "3:4": (3, 4),
           "21:9": (21, 9)}
MAX_REVERSE_SECONDS = 60  # reverse holds every frame in memory


def _even(n: float) -> int:
    return max(2, round(n / 2) * 2)


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


async def _encode(src: Path, dest: Path, *, vf: str | None = None, af: str | None = None,
                  pre: list[str] | None = None, audio: bool = True, extra: list[str] | None = None,
                  cwd: Path | None = None) -> None:
    head = [*(pre or []), "-i", str(src)]
    if vf:
        head += ["-vf", vf]
    if audio and af:
        head += ["-af", af]
    tail = ["-pix_fmt", "yuv420p", *(["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]),
            *(extra or []), "-movflags", "+faststart", str(dest)]
    encoder = await encode_args()
    try:
        await ffmpeg(*head, *encoder, *tail, cwd=cwd)
    except FFmpegError:
        if encoder == X264:
            raise
        await ffmpeg(*head, *X264, *tail, cwd=cwd)  # NVENC can refuse odd sizes or run out of sessions


def target_size(info: Probe, aspect: str) -> tuple[int, int]:
    """Output size for `aspect` with about the same pixel count as the source."""
    aw, ah = ASPECTS[aspect]
    pixels = (info.width or 1280) * (info.height or 720)
    height = (pixels * ah / aw) ** 0.5
    return _even(height * aw / ah), _even(height)


async def reframe(src: Path, dest: Path, aspect: str, fit: Literal["crop", "blur_fill", "pad"] = "crop") -> None:
    info = await probe(src)
    w, h = target_size(info, aspect)
    if fit == "crop":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    elif fit == "pad":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    else:  # the frame over a blurred, filled copy of itself
        vf = (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=30[bg];"
              f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    await _encode(src, dest, vf=vf, audio=info.has_audio)


async def trim(src: Path, dest: Path, start: float, end: float | None) -> None:
    info = await probe(src)
    duration = (info.duration_ms or 0) / 1000
    end = duration if end is None or end <= 0 else min(end, duration)
    if end - start < 0.1:
        raise ValueError("the trimmed clip would be empty; keep at least a tenth of a second")
    await _encode(src, dest, pre=["-ss", f"{start:.3f}", "-to", f"{end:.3f}"], audio=info.has_audio)


def _atempo(factor: float) -> str:
    """atempo only takes 0.5 to 2.0 per stage, so chain stages for bigger changes."""
    stages = []
    while factor > 2.0:
        stages.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        stages.append("atempo=0.5")
        factor /= 0.5
    stages.append(f"atempo={factor:.4f}")
    return ",".join(stages)


async def retime(src: Path, dest: Path, speed: float) -> None:
    if not 0.25 <= speed <= 4:
        raise ValueError("speed must be between 0.25x and 4x")
    info = await probe(src)
    await _encode(src, dest, vf=f"setpts=PTS/{speed:.4f}", af=_atempo(speed), audio=info.has_audio)


async def reverse(src: Path, dest: Path) -> None:
    info = await probe(src)
    if (info.duration_ms or 0) > MAX_REVERSE_SECONDS * 1000:
        raise ValueError(f"reverse works on clips up to {MAX_REVERSE_SECONDS} s; trim it first")
    await _encode(src, dest, vf="reverse", af="areverse", audio=info.has_audio)


async def loop(src: Path, dest: Path, times: int, boomerang: bool = False) -> None:
    info = await probe(src)
    if boomerang:
        # forward then backward, repeated; no sound (reversed audio rarely helps a loop)
        vf = "split[f][r];[r]reverse[b];[f][b]concat=n=2:v=1:a=0"
        if times > 1:
            vf += f",loop=loop={times - 1}:size=32767:start=0"
        await _encode(src, dest, vf=vf, audio=False)
        return
    await _encode(src, dest, pre=["-stream_loop", str(max(0, times - 1))], audio=info.has_audio)


async def interpolate(src: Path, dest: Path, fps: int, method: Literal["motion", "blend"] = "motion") -> None:
    info = await probe(src)
    if method == "motion":
        vf = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    else:
        vf = f"framerate=fps={fps}"
    await _encode(src, dest, vf=vf, audio=info.has_audio)


async def upscale(src: Path, dest: Path, scale: int = 2, sharpen: bool = True) -> None:
    info = await probe(src)
    if (info.width or 0) * scale > 4096 or (info.height or 0) * scale > 4096:
        raise ValueError("the result would be larger than 4096 px; pick a smaller factor")
    vf = f"scale=iw*{scale}:ih*{scale}:flags=lanczos"
    if sharpen:
        vf += ",unsharp=5:5:0.6:5:5:0.0"
    await _encode(src, dest, vf=vf, audio=info.has_audio)


@functools.cache
def has_filter(name: str) -> bool:
    if not shutil.which(FFMPEG) and FFMPEG == "ffmpeg":
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, check=False).stdout.decode(errors="ignore")
    return f" {name} " in out


async def stabilize(src: Path, dest: Path, strength: Literal["gentle", "strong"] = "gentle") -> None:
    info = await probe(src)
    smoothing = 10 if strength == "gentle" else 30
    if has_filter("vidstabdetect"):
        transforms = dest.with_suffix(".trf")
        # vid.stab writes its own file; the path goes inside the filter string, so keep it relative
        await ffmpeg("-i", str(src), "-vf", f"vidstabdetect=shakiness=6:accuracy=15:result={transforms.name}",
                     "-f", "null", "-", cwd=transforms.parent)
        try:
            await _encode(src, dest, vf=f"vidstabtransform=input={transforms.name}:smoothing={smoothing}:zoom=2,"
                                       "unsharp=5:5:0.4:3:3:0.0", audio=info.has_audio, cwd=transforms.parent)
        finally:
            transforms.unlink(missing_ok=True)
        return
    await _encode(src, dest, vf="deshake", audio=info.has_audio)
