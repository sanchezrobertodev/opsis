"""One way to call Claude: structured output, budget checks, a ledger charge per paid call, and a mock mode.

`LLM_MODE=mock` (the default) never touches the network: callers pass a `mock` factory that builds a
plausible result, so every feature works without a key and tests never spend. In live mode a call is
refused up front when it could break the user's spending caps, and its actual token cost is charged
to the same ledger as generations.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import anthropic
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget import modes
from studio.budget.guard import BudgetBlocked, summary
from studio.config import get_settings
from studio.db.models import SpendLedger
from studio.domain.enums import LedgerEntry
from studio.llm import pricing

log = logging.getLogger(__name__)

# Server-side refusal fallback, routed by refusal category (Claude API only).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# the pre-call ceiling counts ~3 characters a token (English runs nearer 4, so it errs high) plus room for the
# output schema, which the API adds to the prompt
CHARS_PER_TOKEN = 3
SCHEMA_TOKENS = 2000


def input_ceiling(system: str, prompt: str) -> int:
    return (len(system) + len(prompt)) // CHARS_PER_TOKEN + SCHEMA_TOKENS


class LLMError(Exception):
    """Claude couldn't produce a result. `message` is safe to show the user."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class LLMUnavailable(LLMError):
    pass


@dataclass
class LLMResult[T: BaseModel]:
    output: T
    model: str
    mock: bool
    cost_usd: Decimal = Decimal("0")
    usage: dict[str, Any] = field(default_factory=dict)


_client: anthropic.AsyncAnthropic | None = None
_client_key = ""


def get_client() -> anthropic.AsyncAnthropic:
    global _client, _client_key
    key = get_settings().anthropic_api_key
    if not key:
        raise LLMUnavailable("Add a Claude key in Settings > Keys to use Claude, or switch Claude to Mock.")
    if _client is None or key != _client_key:  # a key saved in Settings since
        _client, _client_key = anthropic.AsyncAnthropic(api_key=key, timeout=90.0, max_retries=2), key
    return _client


async def is_mock(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Claude's mode in effect: the Settings switch, capped by LLM_MODE in .env (budget/modes.py)."""
    if get_settings().llm_mode == "mock":  # the ceiling: no switch can go past it, so nothing to look up
        return True
    return (await modes.current(session, user_id)).llm == "mock"


async def _check_budget(session: AsyncSession, user_id: uuid.UUID, ceiling: Decimal) -> None:
    s = await summary(session, user_id)
    if not s.live_enabled:
        raise BudgetBlocked("live generation is switched off in settings")
    if s.today_usd + s.reserved_usd + ceiling > s.daily_cap_usd:
        raise BudgetBlocked(f"daily cap ${s.daily_cap_usd:.2f} would be exceeded")
    if s.month_usd + s.reserved_usd + ceiling > s.monthly_cap_usd:
        raise BudgetBlocked(f"monthly cap ${s.monthly_cap_usd:.2f} would be exceeded")


# the pipeline run a Claude call is made for, set by the engine while it runs a step: its charge counts toward the
# run's cap like the run's generations
CURRENT_RUN: ContextVar[uuid.UUID | None] = ContextVar("current_run", default=None)


async def structured[T: BaseModel](
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    purpose: str,
    model: str,
    system: str,
    prompt: str,
    output: type[T],
    mock: Callable[[], T],
    effort: str = "medium",
    max_tokens: int = 8000,
) -> LLMResult[T]:
    """Ask Claude for an `output`-shaped answer. Commits nothing; the caller commits the ledger row."""
    if await is_mock(session, user_id):
        return LLMResult(output=mock(), model=f"{model} (mock)", mock=True)

    client = get_client()
    await _check_budget(session, user_id, pricing.ceiling(model, input_tokens=input_ceiling(system, prompt),
                                                                max_tokens=max_tokens))
    try:
        response = await client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Anthropic rejected the Claude key (Settings > Keys).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMUnavailable(f"This Anthropic key can't use {model}.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Claude is rate-limited right now. Try again in a minute.", retryable=True) from exc
    except anthropic.BadRequestError as exc:
        log.warning("claude %s rejected the request: %s", purpose, exc.message)
        raise LLMError(f"Claude rejected the request: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude returned an error ({exc.status_code}). Try again.", retryable=exc.status_code >= 500) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Couldn't reach Claude. Check the connection and try again.", retryable=True) from exc

    u = response.usage
    usage = {
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cache_read_input_tokens": u.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
    }
    charge = pricing.cost(response.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                          cache_read=usage["cache_read_input_tokens"], cache_write=usage["cache_creation_input_tokens"])
    fell_back = response.model != model
    session.add(SpendLedger(
        user_id=user_id, entry_type=LedgerEntry.charge, amount_usd=charge, provider="anthropic",
        route_id=f"anthropic/{response.model}", llm_call_id=uuid.uuid4(), estimated=fell_back,
        pipeline_run_id=CURRENT_RUN.get(),
        basis={"purpose": purpose, "model": response.model, "requested_model": model, **usage,
               "request_id": response._request_id, "prices_verified": pricing.VERIFIED},
    ))

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise LLMError(f"Claude declined this request{f' ({category})' if category else ''}. Try rewording it.")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude ran out of room before finishing. Try a shorter prompt.")
    parsed = response.parsed_output
    if parsed is None:
        raise LLMError("Claude's answer didn't match the expected shape. Try again.", retryable=True)
    return LLMResult(output=parsed, model=response.model, mock=False, cost_usd=charge, usage=usage)
