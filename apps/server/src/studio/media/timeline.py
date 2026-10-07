"""The film cut: picked takes in order, each trimmed, joined with hard cuts or short fades.

Every clip is first rendered to the same size, frame rate and audio format (silence where a take has no sound),
so the join only has to line them up: `concat` for a cut, `xfade` + `acrossfade` for a fade, which overlaps the
two clips by the fade's length. NVENC when the machine has it, x264 otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from studio.media.ffmpeg import FFmpegError, ffmpeg, probe
from studio.media.ops import encode_args

log = logging.getLogger(__name__)

FPS = 24


@dataclass
class Clip:
    path: Path
    trim_in: float = 0.0
    trim_out: float | None = None
    transition: Literal["cut", "fade"] = "cut"


async def _encode(args: list[str], tail: list[str]) -> None:
    encode = await encode_args()
    try:
        await ffmpeg(*args, *encode, "-pix_fmt", "yuv420p", *tail)
    except FFmpegError:
        if encode[1] != "h264_nvenc":
            raise
        log.warning("NVENC encode failed; retrying with x264")
        await ffmpeg(*args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *tail)


async def normalize_clip(clip: Clip, dest: Path, size: tuple[int, int]) -> float:
    """Trim and conform one take; returns its length in seconds."""
    info = await probe(clip.path)
    full = (info.duration_ms or 0) / 1000
    start = min(clip.trim_in, max(0.0, full - 0.2))
    end = min(clip.trim_out or full, full) if full else (clip.trim_out or 0)
    length = max(0.2, end - start)
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"setsar=1,fps={FPS},format=yuv420p")
    inputs = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(clip.path)]
    if info.has_audio:
        audio = ["-map", "0:v", "-map", "0:a", "-af", "aformat=sample_rates=48000:channel_layouts=stereo"]
    else:
        inputs += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = ["-map", "0:v", "-map", "1:a"]
    await _encode([*inputs, "-vf", vf, *audio], ["-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)])
    return length


def join_graph(lengths: list[float], transitions: list[str], fade: float) -> tuple[str, float]:
    """Filter graph joining inputs 0..n-1 (video+audio) into [v][a]; returns it and the total length."""
    chains, total = [], lengths[0]
    v, a = "0:v", "0:a"
    for i in range(1, len(lengths)):
        d = min(fade, lengths[i - 1] / 2, lengths[i] / 2)
        nv, na = f"v{i}", f"a{i}"
        if transitions[i] == "fade":
            chains.append(f"[{v}][{i}:v]xfade=transition=fade:duration={d:.3f}:offset={total - d:.3f}[{nv}]")
            chains.append(f"[{a}][{i}:a]acrossfade=d={d:.3f}[{na}]")
            total += lengths[i] - d
        else:
            chains.append(f"[{v}][{a}][{i}:v][{i}:a]concat=n=2:v=1:a=1[{nv}][{na}]")
            total += lengths[i]
        v, a = nv, na
    chains.append(f"[{v}]null[v];[{a}]anull[a]")
    return ";".join(chains), round(total, 3)


async def cut_timeline(clips: list[Clip], dest: Path, workdir: Path, *, fade: float = 0.5) -> float:
    """Write the film to `dest` and return its length. The first clip sets the frame size."""
    if not clips:
        raise ValueError("nothing to cut")
    workdir.mkdir(parents=True, exist_ok=True)
    first = await probe(clips[0].path)
    size = ((first.width or 1280) // 2 * 2, (first.height or 720) // 2 * 2)
    parts, lengths = [], []
    for i, clip in enumerate(clips):
        part = workdir / f"part_{i:03d}.mp4"
        lengths.append(await normalize_clip(clip, part, size))
        parts.append(part)
    if len(parts) == 1:
        part = parts[0]
        await ffmpeg("-i", str(part), "-c", "copy", "-movflags", "+faststart", str(dest))
        return lengths[0]
    graph, total = join_graph(lengths, [c.transition for c in clips], fade)
    inputs = [x for p in parts for x in ("-i", str(p))]
    await _encode([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]"],
                  ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)])
    return total
