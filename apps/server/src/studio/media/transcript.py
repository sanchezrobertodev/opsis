"""Transcripts (words with timings) and cutting a song into shots at the pauses in its lyrics.

A transcript asset is JSON: {"words": [{"text", "start", "end"}, ...]} in seconds. Speech-to-text routes decode
their answer into one (`OutputSpec.decode: studio.media.transcript:words_json`).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import anyio

from studio.registry.validate import MediaRef

PAUSE_S = 0.3  # a gap between words this long reads as a pause in the singing


async def words_json(value: Any, *, media: list[MediaRef], dest: Path, workdir: Path) -> str:
    """Decoder hook: a provider's word list (ElevenLabs: word, spacing and audio_event items) becomes a transcript."""
    words = [{"text": str(w["text"]).strip(), "start": float(w["start"]), "end": float(w["end"])}
             for w in value or [] if isinstance(w, dict) and w.get("type", "word") == "word"
             and str(w.get("text", "")).strip() and w.get("start") is not None and w.get("end") is not None]
    await anyio.Path(dest).write_text(json.dumps({"words": words}), encoding="utf-8")
    return "application/json"


def read(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get("words", [])


def mock(length: float) -> dict[str, Any]:
    """What the mock route 'hears': a sung "la" every half second, with a one-second breath after every eight."""
    words, t, n = [], 0.5, 0
    while t + 0.4 <= length - 0.5:
        words.append({"text": "la", "start": round(t, 3), "end": round(t + 0.4, 3)})
        n += 1
        t += 1.5 if n % 8 == 0 else 0.5
    return {"words": words}


def shots(words: list[dict[str, Any]], total: float, *, target_s: float, min_s: float, max_s: float,
          max_shots: int) -> list[dict]:
    """Cut [0, total] into at most `max_shots` consecutive shots of about `target_s` (never over `max_s`, rarely under
    `min_s`), each cut at a pause in the singing near the target, so a shot never breaks mid-word when a pause exists.
    Each shot carries its words, timed from its own start. Needs total <= max_shots * max_s."""
    gaps = [((a["end"] + b["start"]) / 2, b["start"] - a["end"]) for a, b in itertools.pairwise(words)]
    cuts, t0 = [], 0.0
    while total - t0 > max_s:
        # every cut leaves a remainder the shots still allowed can cover, so there are never more than max_shots
        left = max_shots - len(cuts) - 1
        lo, hi = max(t0 + min_s, total - left * max_s), t0 + max_s
        ideal = t0 + max(target_s, (total - t0) / (left + 1))  # what the shots still allowed average out to
        if total - hi < min_s:  # leave the last shot room
            hi = total - min_s
        window = [(at, gap) for at, gap in gaps if lo <= at <= hi]
        if window:
            # a real pause (a breath, a line break) nearest the ideal length, else the longest gap there is
            at = max(window, key=lambda g: (g[1] >= PAUSE_S, -abs(g[0] - ideal) if g[1] >= PAUSE_S else g[1]))[0]
        else:  # one long word or silence: cut at the ideal point, or after the word it falls in
            inside = next((w for w in words if w["start"] < ideal < w["end"]), None)
            at = inside["end"] if inside and inside["end"] <= hi else ideal
        at = round(at, 3)
        cuts.append(at)
        t0 = at
    bounds = [0.0, *cuts, round(total, 3)]
    out = []
    for start, end in itertools.pairwise(bounds):
        mine = [w for w in words if start <= (w["start"] + w["end"]) / 2 < end]
        out.append({"start": start, "end": end, "text": " ".join(w["text"] for w in mine),
                    "words": [{"text": w["text"], "start": round(max(0.0, w["start"] - start), 3),
                               "end": round(min(end, w["end"]) - start, 3)} for w in mine]})
    return out
