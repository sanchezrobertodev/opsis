"""What runs for real: the provider mode (mock / mixed / live) and Claude's (mock / live).

`.env` sets the most a studio may do (PROVIDER_MODE, LLM_MODE). The Settings switch stores a choice on the user's
budget policy that can only be that or safer, and takes effect at once in the API and the worker, with no restart. A machine
whose .env says mock can never be switched live from the app; with nothing chosen, .env applies.

The same policy picks which Claude writes scripts and plans (`smart`) and which helps with prompts (`fast`), from
`llm.pricing.CHOICES` and .env's LLM_SMART_MODEL / LLM_FAST_MODEL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget.guard import get_policy
from studio.config import get_settings
from studio.llm.pricing import CHOICES

PROVIDER_MODES = ("mock", "mixed", "live")
LLM_MODES = ("mock", "live")


def capped(chosen: str | None, ceiling: str, order: tuple[str, ...]) -> str:
    """The chosen mode, unless it's unset, unknown or goes past the ceiling; then the ceiling."""
    if chosen not in order or ceiling not in order:
        return ceiling
    return chosen if order.index(chosen) <= order.index(ceiling) else ceiling


def allowed(order: tuple[str, ...], ceiling: str) -> list[str]:
    """The modes the switch may offer: the ceiling and everything safer."""
    return list(order[: order.index(ceiling) + 1]) if ceiling in order else [ceiling]


def claude_models() -> list[str]:
    """The Claude models Settings may choose: the usual ones and whatever .env names."""
    settings = get_settings()
    return list(dict.fromkeys([*CHOICES, settings.llm_smart_model, settings.llm_fast_model]))


def _claude(chosen: str | None, default: str) -> str:
    return chosen if chosen in claude_models() else default


@dataclass(frozen=True)
class Modes:
    provider: str  # in effect
    provider_ceiling: str  # PROVIDER_MODE in .env
    llm: str
    llm_ceiling: str
    smart_model: str  # scripts, shot plans
    fast_model: str  # prompt help, rewording

    def claude(self, tier: str) -> str:
        return self.smart_model if tier == "smart" else self.fast_model


async def current(session: AsyncSession, user_id: uuid.UUID | None = None) -> Modes:
    settings = get_settings()
    policy = await get_policy(session, user_id or settings.owner_user_id)
    return Modes(
        provider=capped(policy.provider_mode, settings.provider_mode, PROVIDER_MODES),
        provider_ceiling=settings.provider_mode,
        llm=capped(policy.llm_mode, settings.llm_mode, LLM_MODES),
        llm_ceiling=settings.llm_mode,
        smart_model=_claude(policy.llm_smart_model, settings.llm_smart_model),
        fast_model=_claude(policy.llm_fast_model, settings.llm_fast_model),
    )
