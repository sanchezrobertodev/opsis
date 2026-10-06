"""Is a local engine (ComfyUI) up? Cached briefly so routing can ask on every request."""

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
    """provider id -> reachable, for every local provider in the registry."""
    settings = get_settings()
    out: dict[str, bool] = {}
    for provider in reg.providers.values():
        if provider.kind != "local":
            continue
        url = getattr(settings, provider.key_setting or "", "") if provider.key_setting else ""
        out[provider.id] = bool(url) and (await comfy_stats(url)) is not None
    return out
