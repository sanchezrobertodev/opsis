"""Claude token prices for the spend ledger (USD per million tokens).

Verified against the claude-api skill's model table (Anthropic first-party API rates): Opus and Sonnet 2026-10-06,
Haiku 5.5 2026-11-01. Cache writes bill at 1.25x the input rate (5-minute TTL). Fallback models are listed because a
refusal fallback can serve a request on a different model than the one asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

VERIFIED = "2026-11-01"
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True)
class TokenRates:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    long: TokenRates | None = None  # the rate card for a prompt over LONG_PROMPT tokens, where the model has one


LONG_PROMPT = 100_000

RATES: dict[str, TokenRates] = {
    "claude-opus-5-5": TokenRates(Decimal("4"), Decimal("20"), Decimal("0.20")),
    "claude-sonnet-5-5": TokenRates(Decimal("2"), Decimal("10"), Decimal("0.20")),
    "claude-haiku-5-5": TokenRates(Decimal("0.10"), Decimal("0.50"), Decimal("0.01"),
                                   long=TokenRates(Decimal("0.50"), Decimal("2.50"), Decimal("0.05"))),
    # refusal-fallback targets
    "claude-opus-5": TokenRates(Decimal("5"), Decimal("25"), Decimal("0.50")),
    "claude-opus-4-8": TokenRates(Decimal("5"), Decimal("25"), Decimal("0.50")),
    "claude-sonnet-5": TokenRates(Decimal("2"), Decimal("10"), Decimal("0.20")),
}
# What Settings offers for each kind of call, most capable first (.env's model is offered too when it isn't one)
CHOICES = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
MILLION = Decimal(1_000_000)


def rates_for(model: str) -> TokenRates:
    # unknown ids are priced like Opus so the ledger errs high rather than low
    return RATES.get(model, RATES["claude-opus-5-5"])


def cost(model: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> Decimal:
    r = rates_for(model)
    if r.long and input_tokens + cache_read + cache_write > LONG_PROMPT:
        r = r.long
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


# A typical call per ledger purpose, for the price shown by the buttons that make one and for run estimates: (which
# model setting, input tokens, output tokens with thinking). From the averages of 18 live calls on Opus 5.5 and Sonnet
# 5.5 (2026-10), with room for longer episodes, so the web says "about".
TYPICAL_CALLS = {
    "enhance": ("fast", 1_200, 600),  # the model's prompt guide and one prompt; one rewritten prompt
    "faceless.topics": ("fast", 600, 1_000),  # a theme and notes; a handful of ideas
    "faceless.script": ("smart", 1_000, 2_000),  # a brief; a few minutes of narration
    "faceless.song": ("smart", 1_500, 1_500),  # a brief; a song's lyrics in sections
    "faceless.plan": ("smart", 2_500, 2_500),  # the script; a picture prompt per spoken beat
    "vlog.script": ("smart", 1_500, 1_000),  # the idea and the character; a shot list with lines
    "music_video.lyrics": ("smart", 1_500, 1_500),  # a brief; a song's lyrics in sections
    "music_video.plan": ("smart", 2_500, 2_500),  # the lyrics by section; a visual per shot
    "cinema.plan": ("smart", 4_000, 1_500),  # a script and the director's lists; a dozen shots
    "cinema.director": ("smart", 5_000, 1_500),  # the storyboard and the talk so far; a few edits
}


def typical_cost(purpose: str, model: str) -> Decimal:
    _, tokens_in, tokens_out = TYPICAL_CALLS[purpose]
    return cost(model, input_tokens=tokens_in, output_tokens=tokens_out)
