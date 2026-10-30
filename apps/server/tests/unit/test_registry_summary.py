"""The model picker's "from" price: only routes that are set up count, so a model isn't shown free when it isn't."""

from __future__ import annotations

from studio.api.routers.registry import _from_price
from studio.config import get_settings
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())


def test_a_free_route_counts_only_once_it_is_set_up(monkeypatch):
    spec = REG.models["gpt-image-2.5"]  # fal (paid) and Codex (the ChatGPT plan's allowance, $0)
    s = get_settings()
    monkeypatch.setattr(s, "fal_key", "k")
    monkeypatch.setattr(s, "codex_bin", "")
    assert _from_price(REG, spec) > 0
    monkeypatch.setattr(s, "codex_bin", "codex")
    assert _from_price(REG, spec) == 0
    monkeypatch.setattr(s, "fal_key", "")
    monkeypatch.setattr(s, "codex_bin", "")
    assert _from_price(REG, spec) == 0  # nothing set up yet: every route's price, as before
