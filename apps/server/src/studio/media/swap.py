"""Object swap compositing: keep every pixel outside the object's mask exactly as filmed.

Edit models regenerate the whole frame, so walls, faces and light can drift even when only one object was
meant to change. `masked_merge` lays the edited clip over the original through the tracked matte: the matte
is thresholded (white = the object), grown a few pixels to cover the object's new outline, and feathered so
the seam doesn't show. Frame rate, size and length follow the original; its sound is kept.
"""

from __future__ import annotations

import logging
from pathlib import Path

from studio.media.ffmpeg import FFmpegError, ffmpeg, probe
from studio.media.ops import encode_args

log = logging.getLogger(__name__)

MAX_GROW = 12  # each dilation pass grows the matte by one pixel


def merge_graph(width: int, height: int, fps: float, *, grow: int, feather: float) -> str:
    size = f"scale={width}:{height},setsar=1"
    matte = [f"fps={fps:.3f}", size, "format=gray", "lut=c0='if(gt(val,127),255,0)'"]
    matte += ["dilation"] * max(0, min(grow, MAX_GROW))
    if feather > 0:
        matte.append(f"gblur=sigma={feather:.2f}")
    return ";".join([
        f"[0:v]fps={fps:.3f},{size},format=gbrp[base]",
        f"[1:v]fps={fps:.3f},{size},format=gbrp[top]",
        f"[2:v]{','.join(matte)},format=gbrp[matte]",
        "[base][top][matte]maskedmerge,format=yuv420p[v]",
    ])


async def masked_merge(original: Path, edited: Path, matte: Path, dest: Path, *, grow: int = 6,
                       feather: float = 4.0) -> None:
    info = await probe(original)
    width, height = (info.width or 1280) // 2 * 2, (info.height or 720) // 2 * 2
    graph = merge_graph(width, height, info.fps or 24, grow=grow, feather=feather)
    args = ["-i", str(original), "-i", str(edited), "-i", str(matte), "-filter_complex", graph, "-map", "[v]"]
    length = ["-t", f"{info.duration_ms / 1000:.3f}"] if info.duration_ms else ["-shortest"]
    sound = ["-map", "0:a?", "-c:a", "aac", "-b:a", "192k", *length, "-movflags", "+faststart", str(dest)]
    encode = await encode_args()
    try:
        await ffmpeg(*args, *encode, *sound)
    except FFmpegError:
        if encode[1] != "h264_nvenc":
            raise
        log.warning("NVENC encode failed; retrying with x264")
        await ffmpeg(*args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *sound)
