"""Does a take still look like the character? Claude (the fast model) compares a picture, or a clip's middle frame,
with the character's reference pictures and names what differs. The route keeps the answer on the take."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.llm.client import LLMResult, model_for, structured
from studio.providers.mock import parse_tags

MAX_REFERENCES = 4
Aspect = Literal["face", "hair", "build", "outfit", "colors", "marks", "other"]
Verdict = Literal["match", "close", "different"]


class Difference(BaseModel):
    aspect: Aspect
    detail: str = Field(
        description="What the references show and what the checked picture shows instead, briefly"
    )


class Likeness(BaseModel):
    verdict: Verdict = Field(
        description="match: the same character, nothing a viewer would notice; close: clearly "
        "them with small differences; different: a viewer would take them for "
        "someone else, or they aren't in the picture"
    )
    differences: list[Difference] = Field(
        description="Each difference that matters, the biggest first; none for a match"
    )
    summary: str = Field(description="One short sentence for the user")


SYSTEM = """You check that a recurring character looks the same in a new picture as in their reference pictures, for
a studio that keeps characters consistent from picture to picture and video to video.

The pictures before the last are the references; the last one is the picture to check (it may be a frame of a
video). Compare the character only: face, hair, build, outfit, colors and marks (scars, tattoos, freckles, glasses).
Ignore what the prompt asked to change (pose, expression, light, background, framing, and clothes or a picture style
the prompt describes) and differences only the picture's size or blur explains. Never identify anyone or say who they
might be."""


async def check_likeness(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    references: Sequence[uuid.UUID],
    asset_id: uuid.UUID,
    name: str,
    prompt: str = "",
    look: str = "",
) -> LLMResult[Likeness]:
    """Compare the asset with up to MAX_REFERENCES of the character's pictures. The mock answers `match`, or
    `different` when the take's prompt carries [[mock:inconsistent]]."""
    refs = list(references)[:MAX_REFERENCES]
    ask = "\n".join(
        line
        for line in [
            f"The character: {name}" + (f", in their look {look}" if look else "") + ".",
            f"The first {len(refs)} pictures are the references; the last is the one to check."
            if len(refs) > 1
            else "The first picture is the reference; the second is the one to check.",
            f"The prompt the checked picture was made from: {prompt.strip()[:2000]}"
            if prompt.strip()
            else "",
        ]
        if line
    )
    return await structured(
        session,
        user_id,
        purpose="character.check",
        model=await model_for(session, user_id, "fast"),
        system=SYSTEM,
        prompt=ask,
        output=Likeness,
        mock=lambda: mock_likeness(name, prompt),
        effort="low",
        max_tokens=4000,
        images=[*refs, asset_id],
    )


def mock_likeness(name: str, prompt: str) -> Likeness:
    if "inconsistent" in parse_tags(prompt):
        return Likeness(
            verdict="different",
            summary=f"This doesn't look like {name}.",
            differences=[
                Difference(aspect="face", detail="a rounder face and a shorter nose than in the references"),
                Difference(
                    aspect="hair", detail="short dark hair instead of the references' long auburn hair"
                ),
            ],
        )
    return Likeness(verdict="match", differences=[], summary=f"Looks like {name}.")
