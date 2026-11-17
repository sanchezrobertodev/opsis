"""My styles: Claude looks at one to four pictures and writes the look they share as a saved style's name, category,
summary and look (the words a prompt gets, like a catalog style's `prompt`). Nothing is saved here: the web keeps the
answer as a style Element, whose description is the look.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.director.elements import clip
from studio.director.library import StyleCategory
from studio.llm.client import LLMError, LLMResult, model_for, structured
from studio.llm.images import load

LOOK_LIMIT = 200
SUMMARY_LIMIT = 120
NAME_WORDS = 3

SYSTEM = """You name and describe picture styles in Opsis, a self-hosted studio that makes pictures and videos.

You see one to four pictures. Describe the look they share so an image model can make something new in it:
- `look`: comma-separated cues for the look only: medium, palette, light, line, texture and era. At most 200
  characters. Never say what is shown (no subjects, people, animals, places or objects), never name an artist,
  studio, brand, franchise or title, and leave out any lettering the pictures carry.
- `name`: one to three words in sentence case naming the look, like "Faded film" or "Ink and wash".
- `category`: photo, illustration, 3d, painting or retro, whichever fits best.
- `summary`: one short line (under 12 words) for a person browsing a gallery of styles.
If the pictures don't share one look, describe the look most of them have."""


class StyleLook(BaseModel):
    name: str = Field(description="One to three words naming the look, in sentence case")
    category: StyleCategory = Field(description="The gallery section the style belongs in")
    summary: str = Field(description="One short line describing the look, for the gallery")
    look: str = Field(
        description="Comma-separated cues for the look only (medium, palette, light, line, texture, "
        "era), at most 200 characters, with no subjects and no names"
    )


# the mock picks one by the first picture, so the same pictures always get the same answer
MOCK_LOOKS = (
    StyleLook(
        name="Soft watercolor",
        category="painting",
        summary="Loose washes of colour on textured paper.",
        look="watercolor painting, loose wet-on-wet washes, soft pastel palette, visible paper grain, "
        "white highlights left bare",
    ),
    StyleLook(
        name="Faded film",
        category="retro",
        summary="Warm, grainy colour like an old print.",
        look="1970s colour film photograph, warm faded tones, soft grain, gentle halation, lifted blacks",
    ),
    StyleLook(
        name="Clean vector",
        category="illustration",
        summary="Flat shapes with crisp edges.",
        look="flat vector illustration, crisp geometric shapes, limited bright palette, no gradients, "
        "even clean outlines",
    ),
    StyleLook(
        name="Clay render",
        category="3d",
        summary="Soft matte clay under studio light.",
        look="3D clay render, soft matte surfaces, rounded forms, pastel palette, soft studio light, "
        "gentle ambient occlusion",
    ),
)


def _mock(content_hash: str | None) -> StyleLook:
    return MOCK_LOOKS[sum(map(ord, content_hash or "")) % len(MOCK_LOOKS)].model_copy()


async def describe_style(
    session: AsyncSession, user_id: uuid.UUID, asset_ids: Sequence[uuid.UUID]
) -> LLMResult[StyleLook]:
    """Name and describe the look the pictures share (Claude's fast model, low effort). Raises ImageError before
    any call for a picture that can't go (not yours, not ready, not a picture or a clip)."""
    pictures = await load(session, user_id, asset_ids)
    which = "this picture" if len(pictures) == 1 else f"these {len(pictures)} pictures"
    result = await structured(
        session,
        user_id,
        purpose="style.describe",
        model=await model_for(session, user_id, "fast"),
        system=SYSTEM,
        prompt=f"Name and describe the look of {which}.",
        output=StyleLook,
        mock=lambda: _mock(pictures[0].content_hash),
        effort="low",
        max_tokens=4000,
        images=[p.id for p in pictures],
    )
    out = result.output
    out.look = clip(out.look, LOOK_LIMIT, at_comma=True).rstrip(".")
    if not out.look:
        raise LLMError("Claude didn't describe a look. Try again.", retryable=True)
    out.name = " ".join(out.name.split()[:NAME_WORDS])[:40].strip(" .,;") or "My style"
    out.summary = clip(out.summary, SUMMARY_LIMIT)
    return result
