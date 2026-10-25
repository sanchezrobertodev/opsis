"""What's left on each provider account, for the providers whose API says so. Reads only (free), kept for a minute,
each with a short timeout, and one provider failing never hides the others. Keys never leave this module."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import anyio
import httpx

from studio.config import get_settings

CACHE_SECONDS = 60
TIMEOUT = 8.0
RUNWAY_USD_PER_CREDIT = 0.01  # docs.dev.runwayml.com/guides/pricing, checked 2026-10-06

Status = Literal["ok", "unavailable", "no_api", "error"]


@dataclass
class Balance:
    provider: str
    label: str
    status: Status
    link: str
    amount: float | None = None
    unit: Literal["usd", "credits"] | None = None
    usd: float | None = None
    detail: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# provider -> (label, billing page, settings attribute holding its key)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "fal": ("fal.ai", "https://fal.ai/dashboard/billing", "fal_key"),
    "runpod": ("RunPod", "https://console.runpod.io/user/billing", "runpod_api_key"),
    "runway": ("Runway", "https://dev.runwayml.com/", "runway_api_key"),
    "elevenlabs": ("ElevenLabs", "https://elevenlabs.io/app/subscription", "elevenlabs_api_key"),
    "pika": ("Pika", "https://dev.pika.art/billing", "pika_api_key"),
    "anthropic": ("Anthropic (Claude)", "https://platform.claude.com/settings/billing", "anthropic_api_key"),
    "google": ("Google (Gemini, Veo)", "https://console.cloud.google.com/billing", "gemini_api_key"),
    "replicate": ("Replicate", "https://replicate.com/account/billing", "replicate_api_token"),
    "worldlabs": ("World Labs", "https://platform.worldlabs.ai/", "worldlabs_api_key"),
}

_cache: dict[str, Balance] = {}
_cached_at: dict[str, float] = {}


async def _fal(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    r = await c.get("https://api.fal.ai/v1/account/billing", params={"expand": "credits"},
                    headers={"Authorization": f"Key {key}"})
    if r.status_code in (401, 403):
        return {"status": "unavailable", "detail": "this key can't read the balance (fal needs an admin-scoped key)"}
    r.raise_for_status()
    credits = r.json().get("credits") or {}
    amount = credits.get("current_balance")
    return {"amount": amount, "unit": "usd", "usd": amount}


async def _runpod(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    r = await c.post("https://api.runpod.io/graphql", headers={"Authorization": f"Bearer {key}"},
                     json={"query": "query { myself { clientBalance currentSpendPerHr } }"})
    r.raise_for_status()
    me = (r.json().get("data") or {}).get("myself") or {}
    per_hour = me.get("currentSpendPerHr") or 0
    detail = f"spending ${per_hour:.3f} an hour now (storage and running workers)" if per_hour else None
    return {"amount": me.get("clientBalance"), "unit": "usd", "usd": me.get("clientBalance"), "detail": detail}


async def _runway(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    r = await c.get("https://api.dev.runwayml.com/v1/organization",
                    headers={"Authorization": f"Bearer {key}", "X-Runway-Version": "2024-11-06"})
    r.raise_for_status()
    credits = r.json().get("creditBalance")
    return {"amount": credits, "unit": "credits",
            "usd": round(credits * RUNWAY_USD_PER_CREDIT, 2) if isinstance(credits, int | float) else None}


async def _elevenlabs(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    r = await c.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
    r.raise_for_status()
    d = r.json()
    used, limit = d.get("character_count") or 0, d.get("character_limit") or 0
    reset = d.get("next_character_count_reset_unix")
    when = datetime.fromtimestamp(reset, UTC).strftime("%b %d") if reset else None
    plan = str(d.get("tier") or "").replace("_", " ")
    detail = ", ".join(x for x in (f"{plan} plan" if plan else "", f"renews {when}" if when else "") if x) or None
    return {"amount": max(0, limit - used), "unit": "credits", "detail": detail}


async def _pika(c: httpx.AsyncClient, key: str) -> dict[str, Any]:
    from studio.providers.pika import spending_capacity

    r = await c.get("https://api.dev.pika.art/billing/balance", headers={"X-API-Key": key})
    r.raise_for_status()
    amount, invoiced = spending_capacity(r.json())
    usd = amount / 1_000_000
    return {"amount": usd, "unit": "usd", "usd": usd,
            "detail": "remaining invoice-cycle allowance" if invoiced else "prepaid balance including credits"}


READERS = {"fal": _fal, "runpod": _runpod, "runway": _runway, "elevenlabs": _elevenlabs, "pika": _pika}


async def _read(provider: str, key: str, transport: httpx.AsyncBaseTransport | None) -> Balance:
    label, link, _ = PROVIDERS[provider]
    reader = READERS.get(provider)
    if reader is None:
        return Balance(provider, label, "no_api", link, detail="no balance API; see the billing page")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as c:
            found = await reader(c, key)
    except httpx.HTTPStatusError as exc:
        return Balance(provider, label, "error", link, detail=f"the provider answered {exc.response.status_code}")
    except (httpx.HTTPError, ValueError) as exc:
        return Balance(provider, label, "error", link, detail=f"couldn't reach it ({type(exc).__name__})")
    status: Status = found.pop("status", "ok")
    return Balance(provider, label, status, link, **found)


async def balances(*, refresh: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> list[Balance]:
    """Every provider with a key set, in a fixed order; balances read within the last minute are reused."""
    settings = get_settings()
    keyed = [(p, getattr(settings, attr, "") or "") for p, (_, _, attr) in PROVIDERS.items()]
    keyed = [(p, k) for p, k in keyed if k]
    now = time.monotonic()
    stale = [(p, k) for p, k in keyed if refresh or p not in _cache or now - _cached_at[p] > CACHE_SECONDS]

    async def fetch(provider: str, key: str) -> None:
        _cache[provider], _cached_at[provider] = await _read(provider, key, transport), time.monotonic()

    async with anyio.create_task_group() as tg:
        for provider, key in stale:
            tg.start_soon(fetch, provider, key)
    return [_cache[p] for p, _ in keyed]


def clear_cache() -> None:
    _cache.clear()
    _cached_at.clear()
