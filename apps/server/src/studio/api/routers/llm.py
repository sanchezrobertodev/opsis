from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import EnhanceIn, EnhanceOut
from studio.budget.guard import BudgetBlocked
from studio.db.models import User
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.enhance import enhance
from studio.registry.loader import get_registry

router = APIRouter(prefix="/llm", tags=["llm"])


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
