"""What runs for real: the provider mode (mock / mixed / live) and Claude's (mock / live).

`.env` sets the most a studio may do (PROVIDER_MODE, LLM_MODE). The Settings switch stores a choice on the user's
budget policy that can only be that or safer, and takes effect at once in the API and the worker, with no restart. A PC
whose .env says mock can never be switched live from the app; with nothing chosen, .env applies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget.guard import get_policy
from studio.config import get_settings

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


@dataclass(frozen=True)
class Modes:
    provider: str  # in effect
    provider_ceiling: str  # PROVIDER_MODE in .env
    llm: str
    llm_ceiling: str


async def current(session: AsyncSession, user_id: uuid.UUID | None = None) -> Modes:
    settings = get_settings()
    policy = await get_policy(session, user_id or settings.owner_user_id)
    return Modes(
        provider=capped(policy.provider_mode, settings.provider_mode, PROVIDER_MODES),
        provider_ceiling=settings.provider_mode,
        llm=capped(policy.llm_mode, settings.llm_mode, LLM_MODES),
        llm_ceiling=settings.llm_mode,
    )
