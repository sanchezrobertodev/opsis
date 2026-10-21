"""Adapter registry. Provider YAML names an `adapter` key; this maps keys to implementations."""

from __future__ import annotations

import importlib
from functools import cache

from studio.providers.base import ProviderAdapter

# imported only when first used, so a provider's SDK loads only where that provider runs
_ADAPTERS = {
    "mock": "studio.providers.mock:MockAdapter",
    "comfy_local": "studio.providers.comfy_local:ComfyLocalAdapter",
    "local_tools": "studio.providers.local_tools:LocalToolsAdapter",
    "codex": "studio.providers.codex:CodexAdapter",
    "fal": "studio.providers.fal:FalAdapter",
    "google_genai": "studio.providers.google_genai:GoogleGenAIAdapter",
    "replicate": "studio.providers.replicate:ReplicateAdapter",
    "runway": "studio.providers.runway:RunwayAdapter",
    "elevenlabs": "studio.providers.elevenlabs:ElevenLabsAdapter",
    "worldlabs": "studio.providers.worldlabs:WorldLabsAdapter",
    "runpod": "studio.providers.runpod:RunPodAdapter",
    "runpod_comfy": "studio.providers.runpod_comfy:RunPodComfyAdapter",
}


def adapter_implemented(key: str) -> bool:
    return key in _ADAPTERS


@cache
def get_adapter(key: str) -> ProviderAdapter:
    if key not in _ADAPTERS:
        raise LookupError(f"no adapter implemented for {key!r}")
    module, _, cls = _ADAPTERS[key].partition(":")
    return getattr(importlib.import_module(module), cls)()
