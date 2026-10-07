"""Word timings for narration: the alignment that captions and block timing are built from.

Every TTS route ends up with the same shape on its asset (`metadata.alignment.words`):
[{"text": "Hello,", "start": 0.0, "end": 0.42}, ...] in seconds from the start of the clip.
"""

from __future__ import annotations

import re
from typing import Any

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
