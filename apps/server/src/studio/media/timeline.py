"""The film cut: takes and title cards in order, joined with hard cuts or fades, with dialogue, music and SFX
tracks mixed under them.

Timing is counted in frames at 24 fps (`plan`), the same way the Cut tab's preview counts them
(apps/web/src/lib/cut.ts), so the export and the preview have the same length and order. Every item is first
rendered to exactly its frame count at the same size and audio format (silence where a take has no sound); the
join then only lines them up: `concat` for a cut, `xfade` + `acrossfade` for a fade, which overlaps the two items
by the fade's frames. Audio clips are mixed in a second pass; the dialogue bus is written to a file first so the
music can duck under it (splitting a stream to key `sidechaincompress` deadlocks ffmpeg 5's scheduler).
NVENC when the machine has it, x264 otherwise.
"""

from __future__ import annotations

import logging
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio
from PIL import Image, ImageDraw, ImageFont

from studio.media.ffmpeg import FFmpegError, ffmpeg, probe
from studio.media.ops import encode_args

log = logging.getLogger(__name__)

FPS = 24
MIN_FRAMES = 5  # the shortest item (about 0.2 s)
MIN_SECONDS = 0.2
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo"


def to_frames(seconds: float) -> int:
    """Seconds to whole frames, rounding halves up like JavaScript's Math.round (the preview does the same)."""
    return math.floor(seconds * FPS + 0.5)


# ---------------------------------------------------------------- what goes in


@dataclass
class Clip:
    """A take (or any clip) on the picture track."""

    path: Path
    trim_in: float = 0.0
    trim_out: float | None = None
    transition: Literal["cut", "fade"] = "cut"
    volume: float = 1.0  # its own sound; 0 mutes it (its dialogue is on the dialogue track)


@dataclass
class Card:
    """A title card on the picture track: text on a plain background."""

    text: str
    subtext: str = ""
    seconds: float = 3.0
    background: str = "#0d0e10"
    color: str = "#f2efe8"
    transition: Literal["cut", "fade"] = "fade"


@dataclass
class Sound:
    """A clip on an audio track, placed at `start` seconds into the film."""

    path: Path
    track: Literal["dialogue", "music", "sfx"]
    start: float = 0.0
    trim_in: float = 0.0
    length: float | None = None  # seconds used from trim_in; to the clip's end when empty
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0


Item = Clip | Card


@dataclass
class Segment:
    """Where an item sits on the film: its first frame, its length and the frames it overlaps the one before."""

    frames: int
    overlap: int
    start: int


@dataclass
class Plan:
    segments: list[Segment] = field(default_factory=list)
    frames: int = 0

    @property
    def seconds(self) -> float:
        return self.frames / FPS


def clip_seconds(full: float, trim_in: float, trim_out: float | None) -> tuple[float, float]:
    """(start, length) of a take's used part, given its full length (the preview applies the same rule)."""
    start = min(trim_in, max(0.0, full - MIN_SECONDS))
    end = min(trim_out or full, full) if full else (trim_out or 0)
    return start, max(MIN_SECONDS, end - start)


def plan(lengths: list[float], transitions: list[str], fade: float) -> Plan:
    """Lay items out in frames. A fade overlaps the item before by `fade`, but never more than half of either."""
    out = Plan()
    for i, (seconds, transition) in enumerate(zip(lengths, transitions, strict=True)):
        frames = max(MIN_FRAMES, to_frames(seconds))
        overlap = 0
        if i and transition == "fade":
            overlap = min(to_frames(fade), out.segments[-1].frames // 2, frames // 2)
        start = out.frames - overlap
        out.segments.append(Segment(frames, overlap, start))
        out.frames = start + frames
    return out


# ---------------------------------------------------------------- rendering items


async def _encode(args: list[str], tail: list[str]) -> None:
    encode = await encode_args()
    try:
        await ffmpeg(*args, *encode, "-pix_fmt", "yuv420p", *tail)
    except FFmpegError:
        if encode[1] != "h264_nvenc":
            raise
        log.warning("NVENC encode failed; retrying with x264")
        await ffmpeg(*args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *tail)


def _fit(size: tuple[int, int]) -> str:
    w, h = size
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={FPS},format=yuv420p")


async def normalize_clip(clip: Clip, dest: Path, size: tuple[int, int], frames: int | None = None) -> float:
    """Trim and conform one take to exactly `frames` frames (its trimmed length when not given); returns seconds."""
    info = await probe(clip.path)
    start, length = clip_seconds((info.duration_ms or 0) / 1000, clip.trim_in, clip.trim_out)
    frames = frames or max(MIN_FRAMES, to_frames(length))
    seconds = frames / FPS
    # read a little past the end and pad the last frame, so a take a hair short still fills its frames
    inputs = ["-ss", f"{start:.3f}", "-t", f"{seconds + 0.5:.3f}", "-i", str(clip.path)]
    vf = f"{_fit(size)},tpad=stop_mode=clone:stop_duration=1"
    level = f",volume={clip.volume:.3f}" if clip.volume != 1 else ""
    if info.has_audio:
        audio = ["-map", "0:v", "-map", "0:a",
                 "-af", f"aformat=sample_rates=48000:channel_layouts=stereo{level},apad,atrim=0:{seconds:.6f}"]
    else:
        inputs += ["-f", "lavfi", "-t", f"{seconds:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = ["-map", "0:v", "-map", "1:a"]
    await _encode([*inputs, "-vf", vf, *audio, "-frames:v", str(frames)],
                  ["-c:a", "aac", "-b:a", "192k", "-t", f"{seconds:.6f}", str(dest)])
    return seconds


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_card(card: Card, size: tuple[int, int], dest: Path) -> None:
    """The card as a picture: the text centred, the subtext smaller under it."""
    w, h = size
    img = Image.new("RGB", (w, h), card.background)
    draw = ImageDraw.Draw(img)
    title, sub = _font(max(18, h // 11)), _font(max(12, h // 24))
    t_box = draw.multiline_textbbox((0, 0), card.text, font=title, align="center")
    s_box = draw.multiline_textbbox((0, 0), card.subtext, font=sub, align="center") if card.subtext else (0, 0, 0, 0)
    gap = h // 30 if card.subtext else 0
    block = (t_box[3] - t_box[1]) + gap + (s_box[3] - s_box[1])
    y = (h - block) // 2
    draw.multiline_text(((w - (t_box[2] - t_box[0])) // 2, y - t_box[1]), card.text, font=title, fill=card.color,
                        align="center")
    if card.subtext:
        y += (t_box[3] - t_box[1]) + gap
        draw.multiline_text(((w - (s_box[2] - s_box[0])) // 2, y - s_box[1]), card.subtext, font=sub,
                            fill=card.color, align="center")
    img.save(dest)


async def render_card(card: Card, dest: Path, size: tuple[int, int], frames: int, workdir: Path) -> None:
    still = workdir / f"{dest.stem}.png"
    await anyio.to_thread.run_sync(draw_card, card, size, still)
    seconds = frames / FPS
    await _encode(["-loop", "1", "-framerate", str(FPS), "-i", str(still),
                   "-f", "lavfi", "-t", f"{seconds:.6f}", "-i", "anullsrc=r=48000:cl=stereo",
                   "-vf", _fit(size), "-map", "0:v", "-map", "1:a", "-frames:v", str(frames)],
                  ["-c:a", "aac", "-b:a", "192k", "-t", f"{seconds:.6f}", str(dest)])


# ---------------------------------------------------------------- joining and mixing


def join_graph(lengths: list[float], transitions: list[str], fade: float) -> tuple[str, float]:
    """Filter graph joining inputs 0..n-1 (video+audio) into [v][a]; returns it and the total length (seconds)."""
    p = plan(lengths, transitions, fade)
    # one time base for every input: xfade refuses inputs whose time bases differ (a concat's output and a part)
    chains = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[p{i}v];[{i}:a]asetpts=PTS-STARTPTS[p{i}a]"
              for i in range(len(lengths))]
    v, a = "p0v", "p0a"
    for i, seg in enumerate(p.segments[1:], 1):
        nv, na = f"v{i}", f"a{i}"
        if seg.overlap:
            d = seg.overlap / FPS
            chains.append(f"[{v}][p{i}v]xfade=transition=fade:duration={d:.3f}:offset={seg.start / FPS:.3f}[{nv}]")
            chains.append(f"[{a}][p{i}a]acrossfade=d={d:.3f}[{na}]")
        else:
            chains.append(f"[{v}][{a}][p{i}v][p{i}a]concat=n=2:v=1:a=1[{nv}][{na}]")
        v, a = nv, na
    chains.append(f"[{v}]null[v];[{a}]anull[a]")
    return ";".join(chains), round(p.seconds, 3)


async def sound_seconds(sound: Sound) -> float:
    info = await probe(sound.path)
    full = (info.duration_ms or 0) / 1000
    available = max(0.0, full - sound.trim_in)
    return min(sound.length, available) if sound.length else available


def _placed(index: int, sound: Sound, seconds: float, total: float, label: str) -> str | None:
    """One sound trimmed, faded, set to its level and moved to its start; None if it lands after the end."""
    if sound.start >= total or seconds <= 0:
        return None
    seconds = min(seconds, total - sound.start)
    fades = ""
    if sound.fade_in:
        fades += f",afade=t=in:d={min(sound.fade_in, seconds / 2):.3f}"
    if sound.fade_out:
        d = min(sound.fade_out, seconds / 2)
        fades += f",afade=t=out:st={seconds - d:.3f}:d={d:.3f}"
    delay = round(sound.start * 1000)
    return (f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"atrim=start={sound.trim_in:.3f}:duration={seconds:.3f},asetpts=PTS-STARTPTS{fades},"
            f"volume={sound.volume:.3f},adelay={delay}|{delay}[{label}]")


def _bus(labels: list[str], name: str, total: float) -> str:
    joined = "".join(f"[{x}]" for x in labels)
    mix = f"amix=inputs={len(labels)}:duration=longest:normalize=0," if len(labels) > 1 else ""
    return f"{joined}{mix}apad=whole_dur={total:.3f},atrim=0:{total:.3f}[{name}]"


async def mix_tracks(film: Path, sounds: list[Sound], dest: Path, workdir: Path, total: float, *,
                     duck_music: bool = True) -> None:
    """Mix the tracks under the film's own sound and write `dest` (the picture copied as is)."""
    lengths = [await sound_seconds(s) for s in sounds]
    by_track: dict[str, list[tuple[Sound, float]]] = {"dialogue": [], "music": [], "sfx": []}
    for s, seconds in zip(sounds, lengths, strict=True):
        by_track[s.track].append((s, seconds))

    dialogue = workdir / "dialogue.wav"
    if by_track["dialogue"]:
        inputs, chains, labels = [], [], []
        for s, seconds in by_track["dialogue"]:
            chain = _placed(len(inputs) // 2, s, seconds, total, f"d{len(labels)}")
            if chain:
                inputs += ["-i", str(s.path)]
                chains.append(chain)
                labels.append(f"d{len(labels)}")
        if labels:
            chains.append(_bus(labels, "dlg", total))
            await ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", "[dlg]", "-c:a", "pcm_s16le",
                         str(dialogue))
    has_dialogue = dialogue.exists()

    inputs = ["-i", str(film)]
    chains: list[str] = ["[0:a]aformat=sample_rates=48000:channel_layouts=stereo[take]"]
    mixes = ["take"]
    if has_dialogue:
        inputs += ["-i", str(dialogue), "-i", str(dialogue)]  # read twice: once to hear, once to key the ducking
        mixes.append("1:a")
    for track in ("music", "sfx"):
        labels = []
        for s, seconds in by_track[track]:
            label = f"{track[0]}{len(labels)}"
            chain = _placed(len(inputs) // 2, s, seconds, total, label)
            if chain:
                inputs += ["-i", str(s.path)]
                chains.append(chain)
                labels.append(label)
        if not labels:
            continue
        chains.append(_bus(labels, track, total))
        if track == "music" and duck_music and has_dialogue:
            chains.append("[music][2:a]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[musicd]")
            mixes.append("musicd")
        else:
            mixes.append(track)
    if len(mixes) == 1:  # nothing landed inside the film
        shutil.copyfile(film, dest)
        return
    chains.append("".join(f"[{m}]" for m in mixes)
                  + f"amix=inputs={len(mixes)}:duration=first:normalize=0,{LOUDNESS}[out]")
    await ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[out]", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.6f}", "-movflags", "+faststart", str(dest))


async def cut_film(items: list[Item], dest: Path, workdir: Path, *, fade: float = 0.5,
                   sounds: list[Sound] | None = None, duck_music: bool = True,
                   size: tuple[int, int] | None = None) -> Plan:
    """Write the film to `dest` and return its plan. The first take sets the frame size unless one is given."""
    if not items:
        raise ValueError("nothing to cut")
    workdir.mkdir(parents=True, exist_ok=True)
    if size is None:
        first = next((i for i in items if isinstance(i, Clip)), None)
        info = await probe(first.path) if first else None
        size = (((info.width if info else None) or 1280) // 2 * 2, ((info.height if info else None) or 720) // 2 * 2)
    lengths = []
    for item in items:
        if isinstance(item, Card):
            lengths.append(item.seconds)
        else:
            info = await probe(item.path)
            lengths.append(clip_seconds((info.duration_ms or 0) / 1000, item.trim_in, item.trim_out)[1])
    p = plan(lengths, [i.transition for i in items], fade)
    parts = []
    for i, (item, seg) in enumerate(zip(items, p.segments, strict=True)):
        part = workdir / f"part_{i:03d}.mp4"
        if isinstance(item, Card):
            await render_card(item, part, size, seg.frames, workdir)
        else:
            await normalize_clip(item, part, size, seg.frames)
        parts.append(part)

    joined = dest if not sounds else workdir / "picture.mp4"
    if len(parts) == 1:
        await ffmpeg("-i", str(parts[0]), "-c", "copy", "-movflags", "+faststart", str(joined))
    else:
        graph, _ = join_graph(lengths, [i.transition for i in items], fade)
        inputs = [x for part in parts for x in ("-i", str(part))]
        await _encode([*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]"],
                      ["-c:a", "aac", "-b:a", "192k", "-t", f"{p.seconds:.6f}", "-movflags", "+faststart",
                       str(joined)])
    if sounds:
        await mix_tracks(joined, sounds, dest, workdir, p.seconds, duck_music=duck_music)
    return p


async def cut_timeline(clips: list[Clip], dest: Path, workdir: Path, *, fade: float = 0.5) -> float:
    """Takes only, joined (extends and older exports); returns the film's length in seconds."""
    return (await cut_film(list(clips), dest, workdir, fade=fade)).seconds
