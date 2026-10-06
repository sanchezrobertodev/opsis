"""Adapter registry. Provider YAML names an `adapter` key; this maps keys to implementations."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from studio.providers.base import ProviderAdapter


def _mock() -> ProviderAdapter:
    from studio.providers.mock import MockAdapter

    return MockAdapter()


def _comfy_local() -> ProviderAdapter:
    from studio.providers.comfy_local import ComfyLocalAdapter

    return ComfyLocalAdapter()


def _fal() -> ProviderAdapter:
    from studio.providers.fal import FalAdapter

    return FalAdapter()


def _google() -> ProviderAdapter:
    from studio.providers.google_genai import GoogleGenAIAdapter

    return GoogleGenAIAdapter()


def _replicate() -> ProviderAdapter:
    from studio.providers.replicate import ReplicateAdapter

    return ReplicateAdapter()


def _runway() -> ProviderAdapter:
    from studio.providers.runway import RunwayAdapter

    return RunwayAdapter()


_FACTORIES: dict[str, Callable[[], ProviderAdapter]] = {
    "mock": _mock,
    "comfy_local": _comfy_local,
    "fal": _fal,
    "google_genai": _google,
    "replicate": _replicate,
    "runway": _runway,
}


def adapter_implemented(key: str) -> bool:
    return key in _FACTORIES


@cache
def get_adapter(key: str) -> ProviderAdapter:
    try:
        return _FACTORIES[key]()
    except KeyError:
        raise LookupError(f"no adapter implemented for {key!r}") from None
