"""Local media operations: frame grabs and clip assembly. Pure ffmpeg; no database.

Assembly normalises every clip to the first clip's size (letterboxed), a common frame rate and a stereo
audio track (silence where a clip has none), so clips from different models join cleanly. Encoding tries
NVENC first and falls back to x264, which every ffmpeg build has.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from studio.media.ffmpeg import FFmpegError, ffmpeg, probe

log = logging.getLogger(__name__)

_nvenc_ok: bool | None = None


async def extract_frame(src: Path, dest: Path, at: Literal["first", "last"] | float = "last") -> None:
    """Write one frame as an image (format from `dest`'s extension)."""
    if at == "first":
        await ffmpeg("-i", str(src), "-frames:v", "1", str(dest))
    elif at == "last":
        # seek near the end, then keep overwriting the output so the final decoded frame wins
        await ffmpeg("-sseof", "-0.5", "-i", str(src), "-update", "1", str(dest))
        if not dest.exists():  # clips shorter than the seek window
            await ffmpeg("-i", str(src), "-update", "1", str(dest))
    else:
        await ffmpeg("-ss", f"{max(0.0, float(at)):.3f}", "-i", str(src), "-frames:v", "1", str(dest))


async def encode_args() -> list[str]:
    """H.264 encoder flags: NVENC when this machine has it, else x264."""
    global _nvenc_ok
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if _nvenc_ok is False:
        return x264
    if _nvenc_ok is None:
        try:
            await ffmpeg("-f", "lavfi", "-i", "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-")
            _nvenc_ok = True
        except FFmpegError:
            _nvenc_ok = False
            return x264
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"]


async def concat_clips(clips: list[Path], dest: Path, *, fps: float | None = None) -> None:
    """Join clips end to end into an H.264/AAC MP4."""
    if not clips:
        raise ValueError("nothing to assemble")
    first = await probe(clips[0])
    width, height = (first.width or 1280) // 2 * 2, (first.height or 720) // 2 * 2
    rate = fps or first.fps or 24
    inputs: list[str] = []
    chains: list[str] = []
    joins: list[str] = []
    for i, clip in enumerate(clips):
        info = await probe(clip)
        inputs += ["-i", str(clip)]
        chains.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={rate:.3f},format=yuv420p[v{i}]"
        )
        if info.has_audio:
            chains.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            seconds = (info.duration_ms or 0) / 1000
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={seconds:.3f}[a{i}]")
        joins.append(f"[v{i}][a{i}]")
    graph = ";".join([*chains, f"{''.join(joins)}concat=n={len(clips)}:v=1:a=1[v][a]"])
    encode = await encode_args()
    try:
        await ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", *encode,
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest))
    except FFmpegError:
        if encode[1] != "h264_nvenc":
            raise
        log.warning("NVENC encode failed; retrying with x264")
        await ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest))
