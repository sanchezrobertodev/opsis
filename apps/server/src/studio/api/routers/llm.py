from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    ClaudeCallOut,
    ClaudeCallsOut,
    DescribeStyleIn,
    DescribeStyleOut,
    EnhanceIn,
    EnhanceOut,
)
from studio.budget import modes
from studio.budget.features import claude_name
from studio.budget.guard import BudgetBlocked
from studio.db.models import User
from studio.llm import pricing
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.enhance import enhance
from studio.llm.images import ImageError
from studio.llm.styles import describe_style
from studio.registry.loader import get_registry

router = APIRouter(prefix="/llm", tags=["llm"])

def typical_calls(m: modes.Modes) -> dict[str, ClaudeCallOut]:
    return {purpose: ClaudeCallOut(model=m.claude(tier), name=claude_name(m.claude(tier)),
                                   usd=float(pricing.typical_cost(purpose, m.claude(tier))))
            for purpose, (tier, _, _) in pricing.TYPICAL_CALLS.items()}


@router.get("/calls", response_model=ClaudeCallsOut)
async def get_calls(session: AsyncSession = Depends(get_session), user: User = Depends(current_user)) -> ClaudeCallsOut:
    """The Claude model each kind of call uses and what a typical one costs live, for the buttons that make one."""
    m = await modes.current(session, user.id)
    return ClaudeCallsOut(mode=m.llm, verified=pricing.VERIFIED, calls=typical_calls(m))


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


@router.post("/describe-style", response_model=DescribeStyleOut,
             responses={402: {"description": "Spending limit"}, 422: {"description": "A picture that can't go"},
                        502: {"description": "Claude failed"}, 503: {"description": "No Anthropic key"}})
async def post_describe_style(
    body: DescribeStyleIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DescribeStyleOut:
    """Claude looks at one to four pictures and names and describes the look they share, for a saved style. Saves
    nothing. Billed to your budget in live LLM mode."""
    try:
        result = await describe_style(session, user.id, body.asset_ids)
    except ImageError as exc:
        raise HTTPException(422, exc.message) from exc
    except BudgetBlocked as exc:
        raise HTTPException(402, f"Describing the style would go over your spending limit: {exc}") from exc
    except LLMUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    except LLMError as exc:
        await session.commit()  # a refusal or truncated answer was still billed
        raise HTTPException(502, exc.message) from exc
    await session.commit()
    out = result.output
    return DescribeStyleOut(name=out.name, category=out.category, summary=out.summary, look=out.look,
                            model=result.model, mock=result.mock, cost_usd=float(result.cost_usd))
