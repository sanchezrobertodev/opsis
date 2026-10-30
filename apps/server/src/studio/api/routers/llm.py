from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import ClaudeCallOut, ClaudeCallsOut, EnhanceIn, EnhanceOut
from studio.budget import modes
from studio.budget.features import claude_name
from studio.budget.guard import BudgetBlocked
from studio.config import get_settings
from studio.db.models import User
from studio.llm import pricing
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.enhance import enhance
from studio.registry.loader import get_registry

router = APIRouter(prefix="/llm", tags=["llm"])

# A typical call per ledger purpose, for the price shown by the buttons that make one: (model setting, input tokens,
# output tokens with thinking). Guessed from each prompt's size and a usual answer, so the web says "about".
TYPICAL_CALLS = {
    "enhance": ("fast", 1_500, 800),  # the model's prompt guide and one prompt; one rewritten prompt
    "faceless.topics": ("fast", 600, 1_000),  # a theme and notes; a handful of ideas
    "faceless.script": ("smart", 800, 3_000),  # a brief; a few minutes of narration
    "faceless.song": ("smart", 800, 3_000),  # a brief; a song's lyrics in sections
    "faceless.plan": ("smart", 2_000, 4_000),  # the script; a picture prompt per spoken beat
    "cinema.plan": ("smart", 3_000, 6_000),  # a script and the director's lists; a dozen shots
    "cinema.director": ("smart", 5_000, 3_000),  # the storyboard and the talk so far; a few edits
}


def typical_calls() -> dict[str, ClaudeCallOut]:
    settings = get_settings()
    model = {"fast": settings.llm_fast_model, "smart": settings.llm_smart_model}
    return {purpose: ClaudeCallOut(model=model[tier], name=claude_name(model[tier]),
                                   usd=float(pricing.cost(model[tier], input_tokens=tokens_in, output_tokens=tokens_out)))
            for purpose, (tier, tokens_in, tokens_out) in TYPICAL_CALLS.items()}


@router.get("/calls", response_model=ClaudeCallsOut)
async def get_calls(session: AsyncSession = Depends(get_session), user: User = Depends(current_user)) -> ClaudeCallsOut:
    """The Claude model each kind of call uses and what a typical one costs live, for the buttons that make one."""
    return ClaudeCallsOut(mode=(await modes.current(session, user.id)).llm, verified=pricing.VERIFIED,
                          calls=typical_calls())


@router.post("/enhance", response_model=EnhanceOut,
             responses={402: {"description": "Spending limit"}, 502: {"description": "Claude failed"},
                        503: {"description": "No Anthropic key"}})
async def post_enhance(
    body: EnhanceIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> EnhanceOut:
    """Rewrite a prompt for the chosen model with Claude. Billed to your budget in live LLM mode."""
    spec = get_registry().model(body.model_id)
    if spec is None or spec.mode(body.mode) is None:
        raise HTTPException(404, f"unknown model or mode {body.model_id}/{body.mode}")
    try:
        result, warnings = await enhance(session, user.id, spec, body.mode, body.prompt, body.direction)
    except BudgetBlocked as exc:
        raise HTTPException(402, f"Enhancing would go over your spending limit: {exc}") from exc
    except LLMUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    except LLMError as exc:
        await session.commit()  # a refusal or truncated answer was still billed
        raise HTTPException(502, exc.message) from exc
    await session.commit()
    return EnhanceOut(prompt=result.output.prompt, changes=result.output.changes, warnings=warnings,
                      model=result.model, mock=result.mock, cost_usd=float(result.cost_usd))
