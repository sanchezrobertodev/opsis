"""Claude token prices for the spend ledger (USD per million tokens).

Verified 2026-10-06 against the claude-api skill's model table (Anthropic first-party API rates). Cache writes
bill at 1.25x the input rate (5-minute TTL). Fallback models are listed because a refusal fallback can serve
a request on a different model than the one asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

VERIFIED = "2026-10-06"
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True)
class TokenRates:
    input: Decimal
    output: Decimal
    cache_read: Decimal


RATES: dict[str, TokenRates] = {
    "claude-opus-5-5": TokenRates(Decimal("4"), Decimal("20"), Decimal("0.20")),
    "claude-sonnet-5-5": TokenRates(Decimal("2"), Decimal("10"), Decimal("0.20")),
    # refusal-fallback targets
    "claude-opus-5": TokenRates(Decimal("5"), Decimal("25"), Decimal("0.50")),
    "claude-opus-4-8": TokenRates(Decimal("5"), Decimal("25"), Decimal("0.50")),
    "claude-sonnet-5": TokenRates(Decimal("2"), Decimal("10"), Decimal("0.20")),
}
MILLION = Decimal(1_000_000)


def rates_for(model: str) -> TokenRates:
    # unknown ids are priced like Opus so the ledger errs high rather than low
    return RATES.get(model, RATES["claude-opus-5-5"])


def cost(model: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> Decimal:
    r = rates_for(model)
    usd = (
        input_tokens * r.input
        + cache_write * r.input * Decimal("1.25")
        + cache_read * r.cache_read
        + output_tokens * r.output
    ) / MILLION
    return usd.quantize(Decimal("0.000001"))


def ceiling(model: str, *, input_tokens: int, max_tokens: int) -> Decimal:
    """Most a call can cost: every input token uncached and the whole output budget used."""
    return cost(model, input_tokens=input_tokens, output_tokens=max_tokens)
