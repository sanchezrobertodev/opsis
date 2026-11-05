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


def test_a_free_route_whose_runner_is_down_doesnt_make_the_model_look_free(monkeypatch):
    from studio.registry.routing import RoutingEnv

    spec = REG.models["gpt-image-2.5"]
    s = get_settings()
    monkeypatch.setattr(s, "fal_key", "k")
    monkeypatch.setattr(s, "codex_bin", "codex")
    env = RoutingEnv(provider_mode="live", live_allowlist=set(), has_key=lambda _p: True, has_adapter=lambda _a: True)
    assert _from_price(REG, spec, env) == 0
    env.reachable = lambda p: p != "codex"  # the Codex runner is down or signed out: fal draws, and is billed
    assert _from_price(REG, spec, env) > 0
