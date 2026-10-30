"""Is a local engine (ComfyUI) up, and can it run each local route? Cached briefly so routing can ask on every
request."""

from __future__ import annotations

import time
from typing import Any

import httpx

from studio.config import get_settings
from studio.registry.loader import Registry

TTL_SECONDS = 5.0
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


async def comfy_stats(url: str) -> dict[str, Any] | None:
    """ComfyUI /system_stats, or None when the server isn't reachable."""
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and now - hit[0] < TTL_SECONDS:
        return hit[1]
    stats: dict[str, Any] | None
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{url.rstrip('/')}/system_stats")
            stats = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        stats = None
    _cache[url] = (now, stats)
    return stats


async def local_reachability(reg: Registry) -> dict[str, bool]:
    """provider id -> reachable, for every local provider in the registry (each adapter decides how)."""
    from studio.providers.factory import adapter_implemented, get_adapter

    settings = get_settings()
    out: dict[str, bool] = {}
    for provider in reg.providers.values():
        if provider.kind != "local" or not adapter_implemented(provider.adapter):
            continue
        if not provider.configured(settings):
            out[provider.id] = False  # configured off (e.g. COMFYUI_URL empty)
            continue
        out[provider.id] = await get_adapter(provider.adapter).reachable()
    return out


async def local_blockers(reg: Registry, reach: dict[str, bool]) -> dict[tuple[str, str], str]:
    """(route id, mode) -> why a running local engine can't run it (too little GPU memory, missing model files)."""
    from studio.providers.factory import get_adapter

    out: dict[tuple[str, str], str] = {}
    for spec in reg.models.values():
        for route in spec.routes:
            provider = reg.providers.get(route.provider)
            if provider is None or not reach.get(provider.id) or not route.enabled:
                continue
            adapter = get_adapter(provider.adapter)
            for mode in route.modes:
                if why := await adapter.unavailable(route, mode):
                    out[(route.id, mode)] = why
    return out


async def local_state(reg: Registry) -> tuple[dict[str, bool], dict[tuple[str, str], str]]:
    """Which local engines are up, and which of their routes they can't run: what routing needs about them."""
    reach = await local_reachability(reg)
    return reach, await local_blockers(reg, reach)
