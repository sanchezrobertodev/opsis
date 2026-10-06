"""Contract tests generated from the real registry: every model, route and example must be coherent."""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.registry.loader import MOCK_PROVIDER, load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.validate import MediaRef, normalize

REG = load_registry(get_settings().resolve_registry_dir())
EXAMPLES = [(spec, ex) for spec in REG.models.values() for ex in spec.examples]
LIVE_ROUTES = [(spec, r) for spec in REG.models.values() for r in spec.routes if r.provider != MOCK_PROVIDER]


def test_registry_loads_clean():
    assert REG.errors == []
    assert REG.models, "registry is empty"


def test_shutdown_models_absent():
    assert not any(m.family == "sora" for m in REG.models.values()), "Sora 2 API shut down 2026-09-24"


@pytest.mark.parametrize(("spec", "route"), LIVE_ROUTES, ids=lambda x: getattr(x, "id", ""))
def test_live_routes_have_pricing_provenance(spec, route):
    assert route.pricing.verified is not None
    assert route.pricing.source, f"{route.id} needs a pricing source URL"
    assert route.provider in REG.providers


@pytest.mark.parametrize(("spec", "example"), EXAMPLES, ids=lambda x: getattr(x, "name", getattr(x, "id", "")))
def test_examples_normalize_price_and_map(spec, example):
    media = []
    for role, refs in example.media.items():
        for i, _ in enumerate(refs if isinstance(refs, list) else [refs]):
            media.append(MediaRef(role=role, asset_id=f"{role}-{i}", kind=None))
    req = normalize(spec, example.mode, example.prompt, example.params, media)
    routes = [r for r in spec.routes if example.mode in r.modes and r.provider != MOCK_PROVIDER]
    assert routes, f"{spec.id}: example {example.name} has no live route"
    for route in routes:
        usd = float(estimate(route.pricing, req.params, req.prompt).usd)
        low, high = example.expect.get("cost_usd_range", [0, 1e9])
        violates = [k for k, v in route.constraints.items() if not k.startswith("media.")
                    and "options" in v and req.params.get(k) not in v["options"]]
        if not violates:
            assert low <= usd <= high, f"{route.id}: ${usd} outside {low}-{high}"
        staged = {m.role: f"https://example.test/{m.asset_id}" for m in req.media}
        endpoint, payload, _ = build_payload(spec, route, req, staged)
        assert endpoint
        if route.map.prompt and req.prompt:
            assert payload, "payload is empty"


def test_every_model_has_a_smoke_example():
    missing = [m.id for m in REG.models.values() if not any(e.smoke for e in m.examples)]
    assert not missing, f"models without a smoke example: {missing}"
