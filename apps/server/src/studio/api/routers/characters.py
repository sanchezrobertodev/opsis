"""The character designer: its vocabulary and starter characters, and a first draft of the traits from a photo or a
sentence (Claude). Saved characters are Elements (/elements)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.budget.guard import BudgetBlocked
from studio.characters.traits import CharacterCatalog, get_characters
from studio.characters.writer import draft_character
from studio.db.models import User
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.images import ImageError

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/catalog", response_model=CharacterCatalog)
async def catalog() -> CharacterCatalog:
    """The designer's presentations, a person's and a creature's traits (registry/characters/traits.yaml) and the
    starter characters (starters.yaml)."""
    return get_characters()


class CharacterDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: uuid.UUID | None = Field(None, description="A picture of yours (a clip gives its middle frame)")
    text: str | None = Field(None, max_length=500, description="A sentence about the character")

    @model_validator(mode="after")
    def _one(self) -> CharacterDraftIn:
        if (self.asset_id is None) == (not (self.text or "").strip()):
            raise ValueError("send a picture (asset_id) or a sentence (text), one of them")
        return self


class CharacterDraftOut(BaseModel):
    name: str = Field(description="A name for the character, invented unless the sentence gave one")
    traits: dict[str, str] = Field(
        description="The presentation and each of its traits, as the designer saves them: "
        "'' for Any, several picks joined with ', '"
    )
    style: str = Field(description="A catalog style id, or '' for none")
    model: str
    mock: bool = Field(description="LLM_MODE=mock: a canned draft, nothing was billed")
    cost_usd: float


@router.post(
    "/draft",
    response_model=CharacterDraftOut,
    responses={
        402: {"description": "Spending limit"},
        422: {"description": "A picture that can't go"},
        502: {"description": "Claude failed"},
        503: {"description": "No Anthropic key"},
    },
)
async def draft(
    body: CharacterDraftIn, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)
) -> CharacterDraftOut:
    """Claude fills in the designer from a picture or a sentence: traits, a name and a style. Saves nothing. Billed to
    your budget in live LLM mode."""
    try:
        result = await draft_character(session, user.id, asset_id=body.asset_id, text=body.text or "")
    except ImageError as exc:
        raise HTTPException(422, exc.message) from exc
    except BudgetBlocked as exc:
        raise HTTPException(402, f"Asking Claude would go over your spending limit: {exc}") from exc
    except LLMUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    except LLMError as exc:
        await session.commit()  # a refusal or truncated answer was still billed
        raise HTTPException(502, exc.message) from exc
    await session.commit()
    out = result.output
    return CharacterDraftOut(
        name=out.name,
        traits=out.traits,
        style=out.style,
        model=result.model,
        mock=result.mock,
        cost_usd=float(result.cost_usd),
    )
