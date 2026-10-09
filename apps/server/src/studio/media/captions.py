"""Captions from narration word timings: grouping, SRT and styled ASS (current word highlighted), burn-in.

Words come from the TTS alignment (`metadata.alignment.words` on each narration asset), shifted to where
each block starts in the episode. No speech recognition is needed.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

from studio.faceless.catalog import CaptionLook
from studio.media.ffmpeg import ffmpeg
from studio.media.ops import encode_args
from studio.media.speech import Word, estimate_words

SENTENCE_END = (".", "!", "?", '."', '?"', '!"')
CLAUSE_END = (",", ";", ":", "—")
HOLD_S = 0.4  # keep a caption up a moment after its last word, unless the next one starts


@dataclass
class Cue:
    start: float
    end: float
    lines: list[list[Word]] = field(default_factory=list)

    @property
    def words(self) -> list[Word]:
        return [w for line in self.lines for w in line]

    @property
    def text(self) -> str:
        return "\n".join(" ".join(w["text"] for w in line) for line in self.lines)


def place_words(blocks: list[list[Word]], starts: list[float]) -> list[Word]:
    """Shift each block's word timings to the episode timeline."""
    return [{**w, "start": round(w["start"] + t, 3), "end": round(w["end"] + t, 3)}
            for words, t in zip(blocks, starts, strict=True) for w in words]


def fit_words(text: str, seconds: float) -> list[Word]:
    """Estimated timings scaled to a clip's real length, for narration that came without an alignment."""
    words = estimate_words(text)
    if not words or seconds <= 0:
        return words
    scale = seconds / max(words[-1]["end"], 0.01)
    return [{**w, "start": round(w["start"] * scale, 3), "end": round(w["end"] * scale, 3)} for w in words]


def cues(words: list[Word], *, max_chars: int = 30, max_lines: int = 2, max_seconds: float = 4.5) -> list[Cue]:
    """Group words into on-screen captions: break after sentences, after clauses once a caption is fairly full,
    and whenever it would run past `max_lines` lines or `max_seconds`."""
    out: list[Cue] = []
    cue: Cue | None = None
    for w in words:
        if cue is not None:
            last = cue.words[-1]["text"]
            line_len = len(" ".join(x["text"] for x in cue.lines[-1]))
            fullish = len(cue.text) >= max_chars * 0.9 or w["start"] - cue.start > max_seconds * 0.6
            if (last.endswith(SENTENCE_END) and len(cue.words) >= 2) or (last.endswith(CLAUSE_END) and fullish) \
                    or w["end"] - cue.start > max_seconds:
                out.append(cue)
                cue = None
            elif line_len + 1 + len(w["text"]) > max_chars:
                if len(cue.lines) >= max_lines:
                    out.append(cue)
                    cue = None
                else:
                    cue.lines.append([])
        if cue is None:
            cue = Cue(start=w["start"], end=w["end"], lines=[[]])
        cue.lines[-1].append(w)
        cue.end = w["end"]
    if cue is not None:
        out.append(cue)
    out = _absorb_orphans(out, max_chars)
    for a, b in itertools.pairwise(out):
        a.end = round(min(a.end + HOLD_S, b.start), 3)
    if out:
        out[-1].end = round(out[-1].end + HOLD_S, 3)
    return out


def line_cues(sections: list[tuple[float, float, list[str]]], *, max_chars: int = 30, max_lines: int = 2) -> list[Cue]:
    """Song captions: one caption per sung line, the section's (start, end) shared evenly between its lines and
    each line's words spread across its share (for the highlight). A line too long for `max_lines` keeps its
    words on the last line rather than being cut."""
    out: list[Cue] = []
    for start, end, lines in sections:
        lines = [x.strip() for x in lines if x.strip()]
        if not lines or end <= start:
            continue
        share = (end - start) / len(lines)
        for i, line in enumerate(lines):
            t0 = start + i * share
            texts = line.split()
            step = share / len(texts)
            words: list[Word] = [{"text": t, "start": round(t0 + k * step, 3), "end": round(t0 + (k + 1) * step, 3)}
                                 for k, t in enumerate(texts)]
            rows: list[list[Word]] = [[]]
            for w in words:
                if rows[-1] and len(" ".join(x["text"] for x in rows[-1])) + 1 + len(w["text"]) > max_chars \
                        and len(rows) < max_lines:
                    rows.append([])
                rows[-1].append(w)
            out.append(Cue(start=round(t0, 3), end=round(t0 + share, 3), lines=rows))
    return out


def _absorb_orphans(items: list[Cue], max_chars: int) -> list[Cue]:
    """A lone word that ends a sentence joins the caption before it when it fits and follows straight on."""
    out: list[Cue] = []
    for cue in items:
        prev = out[-1] if out else None
        word = cue.words[0] if len(cue.words) == 1 else None
        if prev and word and word["start"] - prev.end <= 0.3 and \
                len(" ".join(w["text"] for w in prev.lines[-1])) + 1 + len(word["text"]) <= max_chars * 1.15:
            prev.lines[-1].append(word)
            prev.end = word["end"]
            continue
        out.append(cue)
    return out


# ---------------------------------------------------------------- SRT


def _srt_time(t: float) -> str:
    ms = round(max(0.0, t) * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(items: list[Cue]) -> str:
    return "\n".join(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n"
                     for i, c in enumerate(items, start=1))


# ---------------------------------------------------------------- ASS


def _ass_time(t: float) -> str:
    cs = round(max(0.0, t) * 100)
    return f"{cs // 360_000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR, ASS's colour order."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _clean(text: str, upper: bool) -> str:
    text = re.sub(r"[{}\\]", "", text)
    return text.upper() if upper else text


def to_ass(items: list[Cue], look: CaptionLook, width: int, height: int) -> str:
    scale = min(width, height) / 1080
    size = round(look.size * scale)
    outline = max(2, round(4 * scale))
    margin_v = round((140 if look.position == "bottom" else 60) * scale)
    align = {"bottom": 2, "middle": 5, "top": 8}[look.position]
    header = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{look.font},{size},{ass_color(look.color)},{ass_color(look.highlight)},"
        f"{ass_color(look.outline)},{ass_color(look.outline, 0x60)},{-1 if look.bold else 0},0,0,0,100,100,0,0,1,"
        f"{outline},{max(1, outline // 2)},{align},{round(80 * scale)},{round(80 * scale)},{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    hi = ass_color(look.highlight)
    events = []
    for cue in items:
        words = cue.words
        for j, current in enumerate(words):
            start = cue.start if j == 0 else current["start"]
            end = words[j + 1]["start"] if j + 1 < len(words) else cue.end
            if end <= start:
                continue
            lines = []
            for line in cue.lines:
                parts = []
                for w in line:
                    text = _clean(w["text"], look.uppercase)
                    parts.append(f"{{\\1c{hi}}}{text}{{\\r}}" if w is current else text)
                lines.append(" ".join(parts))
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{'\\N'.join(lines)}")
    return header + "\n" + "\n".join(events) + "\n"


async def burn_captions(src: Path, ass_text: str, dest: Path, workdir: Path) -> None:
    """Render the captions into the picture. The .ass file sits in `workdir` so the filter path needs no escaping."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "captions.ass").write_text(ass_text, encoding="utf-8")
    encode = await encode_args()
    args = ["-i", str(src), "-vf", "subtitles=captions.ass", "-c:a", "copy", "-movflags", "+faststart"]
    try:
        await ffmpeg(*args[:4], *encode, "-pix_fmt", "yuv420p", *args[4:], str(dest), cwd=workdir)
    except Exception:
        if encode[1] != "h264_nvenc":
            raise
        await ffmpeg(*args[:4], "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     *args[4:], str(dest), cwd=workdir)
