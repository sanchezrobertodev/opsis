"""A character's profile: personality, backstory, catchphrases and how they talk. The writers read it where they write
the character's lines (a vlog, a film's shots, a Faceless star), never what a picture or video model is sent. Claude
can draft one from the name, description, traits and looks; nothing is saved until the editor saves it."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Element
from studio.llm.client import LLMResult, model_for, structured

PERSONALITY, BACKSTORY, SPEECH = 300, 600, 200
CATCHPHRASES, CATCHPHRASE = 5, 120


class ElementProfile(BaseModel):
    """Who a character is beyond their look, for the lines writers give them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    personality: str = Field("", max_length=PERSONALITY, description="Their temperament and how it shows")
    backstory: str = Field("", max_length=BACKSTORY, description="Where they come from and what they want")
    catchphrases: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=CATCHPHRASE)]] = (
        Field([], max_length=CATCHPHRASES, description="Lines they'd say, used now and then")
    )
    speech: str = Field("", max_length=SPEECH, description="How they talk: pace, words, quirks")

    @field_validator("catchphrases", mode="before")
    @classmethod
    def _drop_blank(cls, v: Any) -> Any:
        return [c for c in v if not (isinstance(c, str) and not c.strip())] if isinstance(v, list) else v


def profile_block(profile: Mapping[str, Any] | None) -> str:
    """The lines a writer gets about a character: personality, how they talk and their catchphrases ("" when none is
    set). The backstory stays in the editor, so prompts stay short."""
    p = profile or {}

    def said(key: str) -> str:
        return " ".join(str(p.get(key) or "").split())

    phrases = [" ".join(str(c).split()) for c in p.get("catchphrases") or [] if str(c).strip()]
    return "\n".join(
        line
        for line in [
            f"Personality: {said('personality')}" if said("personality") else "",
            f"How they talk: {said('speech')}" if said("speech") else "",
            "Catchphrases (one now and then, where it fits): " + "; ".join(f'"{c}"' for c in phrases)
            if phrases
            else "",
        ]
        if line
    )


class ProfileDraft(BaseModel):
    personality: str = Field(
        description=f"Two or three traits and how they show, at most {PERSONALITY} characters"
    )
    backstory: str = Field(
        description=f"Where they come from and what they want, at most {BACKSTORY} characters"
    )
    catchphrases: list[str] = Field(
        description=f"Two to {CATCHPHRASES} short lines they'd really say, each at most "
        f"{CATCHPHRASE} characters"
    )
    speech: str = Field(
        description=f"How they talk: pace, vocabulary, sentence length, quirks; at most {SPEECH} characters"
    )


SYSTEM = """You write the profile of a recurring character in Opsis, a studio that makes pictures and videos. Writers
read it when they write the character's lines (a vlog, a film's dialogue, a narrated video), so it is about how the
character behaves and talks, not how they look.

- personality: two or three traits and how they show.
- backstory: where they come from and what they want, in two or three sentences.
- catchphrases: short lines in their own voice they would say more than once; never famous quotes or slogans.
- speech: how they talk: pace, vocabulary, sentence length, quirks.
- Build on what you're given and invent the rest, consistent with it and fit for any audience. Never base the
  character on a real person, even one the name or description resembles."""


def _clip(text: str, limit: int) -> str:
    """At most `limit` characters, cut after the last whole word that fits."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit + 1].rsplit(" ", 1)[0][:limit].rstrip(" ,;:")


def fitted(draft: ProfileDraft) -> ElementProfile:
    """A draft within the profile's limits (Claude's answers can't be held to lengths)."""
    return ElementProfile(
        personality=_clip(draft.personality, PERSONALITY),
        backstory=_clip(draft.backstory, BACKSTORY),
        catchphrases=[c for c in (_clip(c, CATCHPHRASE) for c in draft.catchphrases) if c][:CATCHPHRASES],
        speech=_clip(draft.speech, SPEECH),
    )


def about(element: Element) -> str:
    """What Claude is told about the character: name, description, the designer's traits and the looks."""
    traits = (element.attributes or {}).get("traits") or {}
    picks = (
        [
            f"{k.replace('_', ' ')}: {', '.join(v) if isinstance(v, list) else v}"
            for k, v in traits.items()
            if v and k != "style"
        ]
        if isinstance(traits, dict)
        else []
    )
    looks = [
        f"{v.get('name') or k}: {v.get('description') or ''}".rstrip(": ")
        for k, v in (element.looks or {}).items()
        if isinstance(v, dict)
    ]
    return "\n".join(
        line
        for line in [
            f"Name: {element.name}",
            f"Description: {element.description.strip()}" if (element.description or "").strip() else "",
            "Traits: " + "; ".join(picks) if picks else "",
            "Looks they wear: " + "; ".join(looks) if looks else "",
        ]
        if line
    )


async def draft_profile(
    session: AsyncSession, user_id: uuid.UUID, element: Element
) -> LLMResult[ElementProfile]:
    """A profile for the character (Claude's fast model, low effort). Saves nothing."""
    result = await structured(
        session,
        user_id,
        purpose="character.profile",
        model=await model_for(session, user_id, "fast"),
        system=SYSTEM,
        prompt=f"Write the profile of this character.\n\n{about(element)}",
        output=ProfileDraft,
        mock=lambda: mock_profile(element.name, element.description or ""),
        effort="low",
        max_tokens=4000,
    )
    return replace(result, output=fitted(result.output))


# deterministic stand-ins (LLM_MODE=mock): the same name and description always get the same profile
TEMPERS = (
    (
        "calm and patient, with a dry sense of humour",
        "Speaks slowly in short, plain sentences, and lets a joke land",
    ),
    (
        "curious and restless, always asking one more question",
        "Talks fast, jumps between ideas, ends on a question",
    ),
    ("warm and loyal, quick to cheer others on", "Friendly and open, uses people's names, laughs easily"),
    ("bold and stubborn, never one to back down", "Clipped and direct, few words, no small talk"),
)


def mock_profile(name: str, description: str) -> ProfileDraft:
    first = name.split()[0] if name.split() else "They"
    temper, speech = TEMPERS[sum(map(ord, name + description)) % len(TEMPERS)]
    seen = " ".join(description.split()).rstrip(".")
    return ProfileDraft(
        personality=f"{first} is {temper}.",
        backstory=f"{name} grew up far from the city and still keeps the habits of home. "
        + (f"People remember {first} as {seen[0].lower() + seen[1:]}. " if seen else "")
        + f"Now {first} wants to see everything at least once.",
        catchphrases=[f"Leave it to {first}.", "Let's see where this goes."],
        speech=speech + ".",
    )
