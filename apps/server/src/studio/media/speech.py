"""Word timings for narration: the alignment that captions and block timing are built from.

Every TTS route ends up with the same shape on its asset (`metadata.alignment.words`):
[{"text": "Hello,", "start": 0.0, "end": 0.42}, ...] in seconds from the start of the clip.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from studio.media.ffmpeg import ffmpeg

Word = dict[str, Any]

CHARS_PER_SECOND = 14.5  # about 155 words a minute, a calm narration pace
PAUSES = {".": 0.35, "!": 0.35, "?": 0.35, ";": 0.25, ":": 0.2, ",": 0.15}


def words_from_characters(chars: list[str], starts: list[float], ends: list[float]) -> list[Word]:
    """Group a per-character alignment (ElevenLabs `with-timestamps`) into words."""
    words: list[Word] = []
    text, start, end = "", 0.0, 0.0
    for ch, s, e in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if text:
                words.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
            text = ""
            continue
        if not text:
            start = s
        text += ch
        end = e
    if text:
        words.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
    return words


def estimate_words(text: str, *, chars_per_second: float = CHARS_PER_SECOND) -> list[Word]:
    """Plausible timings for text that was never spoken (the mock provider), with pauses at punctuation."""
    words: list[Word] = []
    t = 0.0
    for token in re.findall(r"\S+", text):
        length = (len(token) + 1) / chars_per_second
        words.append({"text": token, "start": round(t, 3), "end": round(t + length, 3)})
        t += length + PAUSES.get(token[-1], 0.0)
    return words


def spoken_seconds(words: list[Word], tail: float = 0.25) -> float:
    """How long the clip runs: the last word's end plus a little room."""
    return round(words[-1]["end"] + tail, 3) if words else 0.0


def join_graph(count: int, gap: float) -> str:
    """Filter graph for `count` speech inputs, each conformed to 48 kHz mono, with `gap` seconds of silence
    between them, into [out]."""
    parts = [f"[{i}:a]aformat=sample_rates=48000:channel_layouts=mono[s{i}]" for i in range(count)]
    labels = []
    for i in range(count):
        if i:
            parts.append(f"anullsrc=r=48000:cl=mono,atrim=0:{gap:.3f}[g{i}]")
            labels.append(f"[g{i}]")
        labels.append(f"[s{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    return ";".join(parts)


async def join_speech(paths: list[Path], dest: Path, *, gap: float = 0.35) -> None:
    """One dialogue track from several spoken lines, in order, with a short pause between speakers."""

    if len(paths) == 1:
        await ffmpeg("-i", str(paths[0]), "-ar", "48000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k", str(dest))
        return
    inputs = [x for p in paths for x in ("-i", str(p))]
    await ffmpeg(*inputs, "-filter_complex", join_graph(len(paths), gap), "-map", "[out]",
                 "-c:a", "libmp3lame", "-b:a", "128k", str(dest))

