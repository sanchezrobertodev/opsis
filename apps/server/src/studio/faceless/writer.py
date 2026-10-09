"""Faceless Studio writing with Claude: topic ideas, the narration script, and the block plan.

The block plan splits the narration into consecutive blocks (about 8 s for motion clips, 6 s for stills),
each with what the viewer sees. The narration is copied verbatim, so the voiceover covers the whole script
and captions match it word for word. Every call goes through `llm.client.structured()` (mock by default).
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.faceless.catalog import Style, Theme
from studio.llm.client import LLMError, LLMResult, structured
from studio.media.speech import CHARS_PER_SECOND

WORDS_PER_MINUTE = 150
BLOCK_SECONDS = {"motion": 8.0, "stills": 6.0}
Format = Literal["motion", "stills"]


class TopicIdea(BaseModel):
    title: str = Field(description="A specific, intriguing video title under 70 characters")
    angle: str = Field(description="One sentence on what the video covers and why it hooks the viewer")


class TopicIdeas(BaseModel):
    topics: list[TopicIdea] = Field(description="Exactly four distinct ideas")


class Script(BaseModel):
    title: str = Field(description="The video title, under 70 characters")
    narration: str = Field(description="Only the words the narrator says, as plain paragraphs")


class RosterItem(BaseModel):
    name: str = Field(description="Short name used in the visuals, e.g. 'Ada' or 'the lighthouse'")
    kind: Literal["character", "location", "prop"]
    look: str = Field(description="A fixed visual description, repeated whenever it appears so it stays consistent")


class Block(BaseModel):
    narration: str = Field(description="The narration for this block, copied verbatim from the script")
    visual: str = Field(description="One concrete shot: subject, action, setting, composition. No art-style words")
    motion: str = Field("", description="How the shot moves, for animated clips (camera and subject motion)")


class BlockPlan(BaseModel):
    world: str = Field(description="One sentence fixing this episode's visual world: era, place, palette, light")
    roster: list[RosterItem] = Field(default_factory=list, description="Recurring characters, places and props")
    blocks: list[Block]


# ---------------------------------------------------------------- prompts

TOPICS_SYSTEM = """You suggest topics for a faceless narrated video channel (illustrated visuals with a voiceover).
Each idea must be specific enough to make a 1 to 5 minute video, accurate, and new to the viewer.
Return exactly four distinct ideas that fit the channel's theme."""

SCRIPT_SYSTEM = """You write narration scripts for faceless videos: a voice actor reads your words over illustrated
visuals. Write only what the narrator says: no headings, scene labels, stage directions, sound cues, emojis or
markdown. Plain paragraphs. Hit the target length closely, since the video's length follows the script.
Be accurate; if the topic is factual and the record is uncertain, say so in the narration."""

PLAN_SYSTEM = """You plan the visuals for a narrated faceless video.

Split the narration into consecutive blocks, cutting only at sentence ends. Copy the narration verbatim: the
blocks together must contain the whole script, in order, with nothing added, dropped or reworded.

For each block, describe one shot the viewer sees while that narration plays:
- Concrete and visual: subject, action, setting, composition. Show what the words are about, literally or as
  a clear visual metaphor.
- Never mention the art style, medium, colour grading or rendering; the style is added separately.
- No text, captions, letters, numbers, logos or signs in the picture.
- When a recurring character, place or prop appears, write its roster name, and keep its look identical.
- Vary the framing between blocks (wide, medium, close-up, overhead) so the cut stays lively.

List the recurring characters, places and props in `roster` with one fixed visual description each. Give
`world` one sentence that pins the look of this episode's world (era, place, palette, light)."""


def _target_words(minutes: float) -> int:
    return round(minutes * WORDS_PER_MINUTE)


def block_seconds(fmt: Format) -> float:
    return BLOCK_SECONDS[fmt]


# ---------------------------------------------------------------- calls


async def suggest_topics(session: AsyncSession, user_id: uuid.UUID, *, theme: Theme, channel: str = "",
                         notes: str = "") -> LLMResult[TopicIdeas]:
    prompt = "\n".join(line for line in [
        f"Theme: {theme.label}. {theme.summary}",
        f"Channel: {channel}" if channel else "",
        f"Channel notes: {notes}" if notes else "",
    ] if line)
    result = await structured(
        session, user_id, purpose="faceless.topics", model=get_settings().llm_fast_model, system=TOPICS_SYSTEM,
        prompt=prompt, output=TopicIdeas, mock=lambda: _mock_topics(theme), effort="low", max_tokens=2000)
    result.output.topics = result.output.topics[:4]
    return result


async def write_script(session: AsyncSession, user_id: uuid.UUID, *, topic: str, theme: Theme, minutes: float,
                       language: str = "English", notes: str = "") -> LLMResult[Script]:
    words = _target_words(minutes)
    prompt = "\n".join(line for line in [
        f"Topic: {topic}",
        f"Theme guidance: {theme.script_guidance}",
        f"Length: about {words} words (it will be read at about {WORDS_PER_MINUTE} words a minute).",
        f"Language: {language}.",
        f"Extra notes from the creator: {notes}" if notes else "",
    ] if line)
    result = await structured(
        session, user_id, purpose="faceless.script", model=get_settings().llm_smart_model, system=SCRIPT_SYSTEM,
        prompt=prompt, output=Script, mock=lambda: _mock_script(topic, words), effort="medium", max_tokens=8000)
    result.output.narration = tidy_narration(result.output.narration)
    if not result.output.narration:
        raise LLMError("Claude returned an empty script. Try again.", retryable=True)
    return result


async def plan_blocks(session: AsyncSession, user_id: uuid.UUID, *, script: str, title: str, style: Style,
                      fmt: Format) -> LLMResult[BlockPlan]:
    seconds = block_seconds(fmt)
    prompt = "\n".join([
        f"Title: {title}",
        f"Format: {'animated clips' if fmt == 'motion' else 'still images'}, about {seconds:.0f} seconds per block "
        f"(roughly {round(seconds * CHARS_PER_SECOND / 6)} words).",
        f"Art style, for your understanding only (don't repeat it): {style.label}: {style.summary}",
        "" if fmt == "motion" else "Leave `motion` empty: these are still images.",
        f"\nNarration:\n{script}",
    ])
    result = await structured(
        session, user_id, purpose="faceless.plan", model=get_settings().llm_smart_model, system=PLAN_SYSTEM,
        prompt=prompt, output=BlockPlan, mock=lambda: _mock_plan(script, fmt), effort="medium", max_tokens=16000)
    plan = result.output
    plan.blocks = [b for b in plan.blocks if b.narration.strip()]
    texts = narration_from_script(script, [b.narration for b in plan.blocks])
    if texts is None:
        raise LLMError("Claude's block plan didn't keep the script word for word. Try again.", retryable=True)
    for block, text in zip(plan.blocks, texts, strict=True):
        block.narration = text
    return result


# ---------------------------------------------------------------- text helpers


def tidy_narration(text: str) -> str:
    """Drop markdown and stage directions a writer might slip in; keep paragraphs."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(#+|[-*]|\d+\.)\s+", "", line)
        line = re.sub(r"\[[^\]]*\]|\*\*|__", "", line).strip()
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s.strip()]


def _words(text: str) -> int:
    return len(text.split())


WORD = re.compile(r"[\w']+")
CURLY_APOSTROPHE = "\u2019"


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text.replace(CURLY_APOSTROPHE, "'"))]


def narration_from_script(script: str, blocks: list[str]) -> list[str] | None:
    """Each block's narration cut from the approved script itself, so what is spoken is exactly what was approved.

    The blocks must hold the script's words in order, none added, dropped or repeated (case and punctuation may
    differ); otherwise None. Cuts fall just before each block's first word, so punctuation stays with its sentence.
    """
    counts = [len(_tokens(t)) for t in blocks]
    if not blocks or 0 in counts or _tokens(" ".join(blocks)) != _tokens(script):
        return None
    starts = [m.start() for m in WORD.finditer(script.replace(CURLY_APOSTROPHE, "'"))]
    cuts, n = [], 0
    for count in counts:
        cuts.append(starts[n])
        n += count
    ends = [*cuts[1:], len(script)]
    return [" ".join(script[a:b].split()) for a, b in zip(cuts, ends, strict=True)]


def split_narration(text: str, seconds: float) -> list[str]:
    """Group sentences into blocks of about `seconds` of speech."""
    budget = seconds * CHARS_PER_SECOND
    blocks: list[str] = []
    current = ""
    for s in sentences(text):
        if current and len(current) + 1 + len(s) > budget * 1.25:
            blocks.append(current)
            current = s
        else:
            current = f"{current} {s}".strip()
        if len(current) >= budget:
            blocks.append(current)
            current = ""
    if current:
        blocks.append(current)
    return blocks


# ---------------------------------------------------------------- mock results (LLM_MODE=mock)

_MOCK_TOPICS = {
    "education": ["Why the sky turns red at sunset", "How bees agree on a new home", "What makes bread rise",
                  "Why we can't tickle ourselves"],
    "history": ["The night the lighthouse went dark", "The 1858 summer London couldn't breathe",
                "The mapmaker who invented an island", "How a lost glove started a postal service"],
    "kids": ["The snail who wanted to be fast", "Why octopuses have three hearts", "The cloud that forgot to rain",
             "How penguins keep their eggs warm"],
    "storytelling": ["The keeper of the last lighthouse", "A letter delivered fifty years late",
                     "The clockmaker's final commission", "The town where nobody could whistle"],
}

_FILLER = [
    "It started quietly, the way the biggest changes often do.",
    "Nobody at the time could have guessed what would follow.",
    "Look closer, and the details tell a stranger story.",
    "Each step depended on the one before it.",
    "And then, just as things seemed settled, everything shifted.",
    "People still argue about why it happened this way.",
    "The answer turns out to be simpler than it first appears.",
    "That small moment changed what came after.",
]


def _mock_topics(theme: Theme) -> TopicIdeas:
    titles = _MOCK_TOPICS.get(theme.id, _MOCK_TOPICS["storytelling"])
    return TopicIdeas(topics=[TopicIdea(title=t, angle=f"A short {theme.label.lower()} video: {t.lower()}.")
                              for t in titles])


def _mock_script(topic: str, words: int) -> Script:
    out = [f"This is the story of {topic.rstrip('.').lower()}."]
    i = 0
    while _words(" ".join(out)) < words:
        out.append(_FILLER[i % len(_FILLER)])
        i += 1
    paragraphs = [" ".join(out[j:j + 4]) for j in range(0, len(out), 4)]
    return Script(title=topic[:70], narration="\n\n".join(paragraphs))


def _mock_plan(script: str, fmt: Format) -> BlockPlan:
    framings = ["Wide shot", "Medium shot", "Close-up", "Overhead view"]
    blocks = [
        Block(narration=text,
              visual=f"{framings[i % 4]} of the narrator's subject: {' '.join(text.split()[:10]).rstrip('.,;')}",
              motion="slow push in" if fmt == "motion" else "")
        for i, text in enumerate(split_narration(script, block_seconds(fmt)))
    ]
    return BlockPlan(world="A quiet coastal town in the late 1800s, muted blues and warm lamplight.",
                     roster=[RosterItem(name="the keeper", kind="character",
                                        look="an older woman in a dark wool coat with a grey braid")],
                     blocks=blocks)


# ---------------------------------------------------------------- songs (the Kids song mode)

SECONDS_PER_LINE = 4.0  # one sung line at a sing-along tempo
SECTION_PAD_S = 2.0  # a bar of music into each section
MAX_SECTIONS = 12  # the `sections` param's limit on Eleven Music
LINES_PER_SECTION = 4  # what a song's sections average
MAX_SONG_S = 300  # Eleven Music's `duration` limit in the registry


class SongSection(BaseModel):
    name: str = Field(description="Verse 1, Chorus, Bridge...")
    lines: list[str] = Field(description="The sung lines, 2 to 6, short and singable")
    picture: str = Field(description="One concrete picture shown while this section plays: subject, action, setting. "
                                     "No art-style words, no text in the picture")


class Song(BaseModel):
    title: str = Field(description="The song title, under 70 characters")
    music: str = Field(description="How it sounds, as comma-separated styles: genre, instruments, voice, tempo")
    world: str = Field(description="One sentence fixing the pictures' world: place, palette, light")
    sections: list[SongSection]


SONG_SYSTEM = """You write short songs for a children's video channel: the lyrics are sung over illustrated pictures.

Write a song with verses and a repeated chorus (repeat the chorus section in full each time it comes back), in at
most 12 sections. Lines
are short, rhythmic and singable, with simple rhymes and words a 5 to 9 year old knows. Warm, playful, nothing
scary or sad, and one clear idea or fact the child takes away. Hit the target length closely: each line takes about
four seconds to sing.

For each section give one picture to show while it plays: concrete subject, action and setting, no art-style words,
no text, letters or numbers in the picture. Keep recurring characters looking the same. Give `music` as a few
comma-separated styles (genre, instruments, voice, tempo) and `world` one sentence pinning the pictures' look."""


def song_lines(minutes: float) -> int:
    """About how many sung lines fill `minutes`, counting each section's bar in, within the longest song."""
    seconds = min(minutes * 60, MAX_SONG_S)
    return max(6, int(seconds / (SECONDS_PER_LINE + SECTION_PAD_S / LINES_PER_SECTION)))


def fit_song(seconds: list[float], limit: float) -> list[float]:
    """Section lengths that fit within the model's longest song: sung a little faster, never under 3 s each."""
    total = sum(seconds)
    if total <= limit:
        return seconds
    return [max(3.0, int(s * limit / total * 10) / 10) for s in seconds]


def section_seconds(lines: list[str]) -> float:
    """How long a section is sung for: a bar in, then about four seconds a line (3 to 60 s)."""
    return round(min(60.0, max(3.0, SECTION_PAD_S + SECONDS_PER_LINE * len(lines))), 1)


async def write_song(session: AsyncSession, user_id: uuid.UUID, *, topic: str, theme: Theme, minutes: float,
                     language: str = "English", notes: str = "") -> LLMResult[Song]:
    lines = song_lines(minutes)
    prompt = "\n".join(line for line in [
        f"Topic: {topic}",
        f"Theme guidance: {theme.script_guidance}",
        f"Length: about {lines} sung lines in all, choruses included.",
        f"Music: the channel's sound is {theme.music}.",
        f"Language: {language}.",
        f"Extra notes from the creator: {notes}" if notes else "",
    ] if line)
    result = await structured(
        session, user_id, purpose="faceless.song", model=get_settings().llm_smart_model, system=SONG_SYSTEM,
        prompt=prompt, output=Song, mock=lambda: _mock_song(topic), effort="medium", max_tokens=8000)
    song = result.output
    song.sections = [s for s in song.sections if any(x.strip() for x in s.lines)][:MAX_SECTIONS]
    if not song.sections:
        raise LLMError("Claude wrote a song without lyrics")
    return result


def song_from_text(text: str, title: str) -> Song:
    """Your own lyrics: a section per paragraph, a line per line; a paragraph starting "[Chorus]" is named so."""
    sections = []
    for i, para in enumerate(p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()):
        lines = [x.strip() for x in para.splitlines() if x.strip()]
        name = f"Verse {i + 1}"
        if lines and (m := re.fullmatch(r"\[(.+?)\]", lines[0])):
            name, lines = m.group(1), lines[1:]
        if lines:
            sections.append(SongSection(name=name[:100], lines=lines[:30], picture=" ".join(lines)[:300]))
    return Song(title=title, music="", world="", sections=sections[:MAX_SECTIONS])


def song_text(song: Song) -> str:
    """The lyrics as one text: for the approval step and the captions."""
    return "\n\n".join(f"[{s.name}]\n" + "\n".join(s.lines) for s in song.sections)


def _mock_song(topic: str) -> Song:
    subject = topic.strip().rstrip(".") or "the sea"
    chorus = SongSection(name="Chorus", lines=[f"Sing about {subject}", "Clap your hands along"],
                         picture="children dancing in a circle on a sunny beach")
    return Song(title=f"The {subject} song"[:70], music="cheerful children's pop, ukulele, glockenspiel, bright vocals",
                world="A bright seaside town in summer, soft pastel colours, gentle sunlight.",
                sections=[SongSection(name="Verse 1", lines=[f"Have you heard of {subject}?", "Let me sing it to you"],
                                      picture="a smiling lighthouse on a green hill by the sea"),
                          chorus,
                          SongSection(name="Verse 2", lines=["Now you know it too", "Sing it all day through"],
                                      picture="a small boat with a striped sail on calm water"),
                          chorus])
