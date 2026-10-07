"""Episode assembly for narrated videos: one visual per narration block, voiceover, ducked music, loudness.

Each block lasts as long as its narration plus a short breath. Stills get a slow Ken Burns move; clips are
cropped to fill the frame and fitted to the block (trimmed, slowed by up to 1.5x, then held on the last
frame). Segments are encoded with identical settings, so they join without re-encoding. The audio is the
narration blocks laid end to end, music ducked under the voice, normalised to about -14 LUFS.

Pure ffmpeg, no database. Every call runs through `media.ffmpeg` (threaded subprocess, Windows-safe).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from studio.media.ffmpeg import FFmpegError, ffmpeg, probe
from studio.media.ops import encode_args

log = logging.getLogger(__name__)

GAP_S = 0.35  # breath between blocks
TAIL_S = 1.2  # room after the last line
MAX_SLOWDOWN = 1.5
KEN_BURNS = ("in", "out", "left", "right")


@dataclass
class Block:
    visual: Path
    kind: Literal["image", "video"]
    narration: Path
    seconds: float  # how long the narration runs


@dataclass
class Timeline:
    starts: list[float]
    durations: list[float]

    @property
    def total(self) -> float:
        return round(self.starts[-1] + self.durations[-1], 3) if self.starts else 0.0


def frame_size(aspect: str, resolution: str = "1080p") -> tuple[int, int]:
    short = 720 if resolution == "720p" else 1080
    long = short * 16 // 9
    return (short, long) if aspect == "9:16" else (long, short)


def timeline(seconds: list[float]) -> Timeline:
    starts, durations, t = [], [], 0.0
    for i, s in enumerate(seconds):
        d = round(max(0.5, s) + (TAIL_S if i == len(seconds) - 1 else GAP_S), 3)
        starts.append(round(t, 3))
        durations.append(d)
        t += d
    return Timeline(starts, durations)


def _cover(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def ken_burns(move: str, frames: int, w: int, h: int, fps: int) -> str:
    """Zoom or pan across a still. The image is scaled to 2x first so the move doesn't stair-step."""
    n = max(frames - 1, 1)
    centre = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    z, xy = {
        "in": (f"'1+0.12*on/{n}'", centre),
        "out": (f"'1.12-0.12*on/{n}'", centre),
        "left": ("1.12", f"x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'"),
        "right": ("1.12", f"x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'"),
    }[move]
    return f"{_cover(w * 2, h * 2)},zoompan=z={z}:{xy}:d={frames}:s={w}x{h}:fps={fps}"


async def _encode(args: list[str], dest: Path, fps: int) -> None:
    encode = await encode_args()
    tail = [*encode, "-pix_fmt", "yuv420p", "-r", str(fps), "-an", str(dest)]
    try:
        await ffmpeg(*args, *tail)
    except FFmpegError:
        if encode[1] != "h264_nvenc":
            raise
        log.warning("NVENC encode failed; retrying with x264")
        await ffmpeg(*args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *tail[len(encode):])


async def render_segment(block: Block, seconds: float, dest: Path, *, size: tuple[int, int], fps: int,
                         index: int = 0) -> None:
    w, h = size
    frames = max(1, round(seconds * fps))
    if block.kind == "image":
        vf = f"{ken_burns(KEN_BURNS[index % len(KEN_BURNS)], frames, w, h, fps)},format=yuv420p"
        await _encode(["-i", str(block.visual), "-vf", vf, "-frames:v", str(frames)], dest, fps)
        return
    info = await probe(block.visual)
    length = (info.duration_ms or 0) / 1000 or seconds
    chain = []
    if length < seconds:
        slow = min(MAX_SLOWDOWN, seconds / length)
        chain.append(f"setpts={slow:.4f}*PTS")
        if length * slow < seconds:
            chain.append(f"tpad=stop_mode=clone:stop_duration={seconds - length * slow + 0.2:.3f}")
    chain += [f"fps={fps}", _cover(w, h), "format=yuv420p"]
    await _encode(["-i", str(block.visual), "-vf", ",".join(chain), "-t", f"{seconds:.3f}"], dest, fps)


async def assemble_episode(blocks: list[Block], dest: Path, workdir: Path, *, size: tuple[int, int],
                           fps: int = 24, music: Path | None = None, music_db: float = -14.0) -> Timeline:
    """Write the clean master (no captions) to `dest` and return where each block starts."""
    if not blocks:
        raise ValueError("nothing to assemble")
    workdir.mkdir(parents=True, exist_ok=True)
    tl = timeline([b.seconds for b in blocks])

    segments = []
    for i, (block, seconds) in enumerate(zip(blocks, tl.durations, strict=True)):
        seg = workdir / f"seg_{i:03d}.mp4"
        await render_segment(block, seconds, seg, size=size, fps=fps, index=i)
        segments.append(seg)
    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    await ffmpeg("-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", "video.mp4", cwd=workdir)

    # The voice track goes to a file first: splitting a concat inside one graph to key the ducking deadlocks
    # ffmpeg 5's filter scheduler, so the mix reads the file twice instead.
    inputs, chains = [], []
    for i, (block, seconds) in enumerate(zip(blocks, tl.durations, strict=True)):
        inputs += ["-i", str(block.narration)]
        chains.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={seconds:.3f},"
                      f"atrim=0:{seconds:.3f}[n{i}]")
    chains.append(f"{''.join(f'[n{i}]' for i in range(len(blocks)))}concat=n={len(blocks)}:v=0:a=1[voice]")
    await ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", "[voice]", "-c:a", "pcm_s16le", "voice.wav",
                 cwd=workdir)

    loud = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo"
    if music:
        fade_at = max(0.0, tl.total - 3)
        # loop short scores, but stop reading at the episode's end (an endless input never lets ffmpeg finish)
        inputs = ["-i", "voice.wav", "-i", "voice.wav", "-stream_loop", "-1", "-t", f"{tl.total:.3f}", "-i", str(music)]
        graph = ";".join([
            f"[3:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={music_db}dB,atrim=0:{tl.total:.3f},"
            f"afade=t=in:d=1.5,afade=t=out:st={fade_at:.3f}:d=3[bed]",
            "[bed][2:a]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[ducked]",
            f"[1:a][ducked]amix=inputs=2:duration=first:normalize=0,{loud}[out]",
        ])
    else:
        inputs = ["-i", "voice.wav"]
        graph = f"[1:a]{loud}[out]"
    await ffmpeg("-i", "video.mp4", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[out]",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{tl.total:.3f}",
                 "-movflags", "+faststart", str(dest), cwd=workdir)
    return tl
