"""Adapter registry. Provider YAML names an `adapter` key; this maps keys to implementations."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from studio.providers.base import ProviderAdapter


def _mock() -> ProviderAdapter:
    from studio.providers.mock import MockAdapter

    return MockAdapter()


# Live adapters (fal, google_genai, replicate, runway, …) are added in P1.
_FACTORIES: dict[str, Callable[[], ProviderAdapter]] = {
    "mock": _mock,
}


def adapter_implemented(key: str) -> bool:
    return key in _FACTORIES


@cache
def get_adapter(key: str) -> ProviderAdapter:
    try:
        return _FACTORIES[key]()
    except KeyError:
        raise LookupError(f"no adapter implemented for {key!r}") from None
