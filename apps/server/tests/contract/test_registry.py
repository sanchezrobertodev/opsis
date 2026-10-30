"""Contract tests generated from the real registry: every model, route and example must be coherent."""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.registry.loader import MOCK_PROVIDER, load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.validate import MediaRef, normalize
from studio.registry.virtual import resolve as resolve_virtual

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
            # stand-in pixel size, as the API fills it from the asset (routes that place clicks need it)
            media.append(MediaRef(role=role, asset_id=f"{role}-{i}", kind=None, width=1280, height=720))
    # a virtual model's example runs on its engine's base model (director params become direction)
    v = resolve_virtual(REG, spec, example.mode, example.params, None)
    spec = v.spec
    assert all("left out" not in w for w in v.warnings), v.warnings
    req = normalize(spec, v.mode, example.prompt, v.params, media)
    routes = [r for r in spec.routes if req.mode in r.modes and r.provider != MOCK_PROVIDER]
    assert routes, f"{spec.id}: example {example.name} has no live route"
    for route in routes:
        usd = float(estimate(route.pricing, req.params, req.prompt, req.media, req.mode).usd)
        low, high = example.expect.get("cost_usd_range", [0, 1e9])
        violates = [k for k, v in route.constraints.items() if not k.startswith("media.")
                    and "options" in v and req.params.get(k) not in v["options"]]
        if REG.providers[route.provider].kind == "local":
            assert usd == 0, f"{route.id}: a local route is free, not ${usd}"  # the range prices the paid routes
        elif not violates:
            assert low <= usd <= high, f"{route.id}: ${usd} outside {low}-{high}"
        staged = {m.role: f"https://example.test/{m.asset_id}" for m in req.media}
        endpoint, payload, _ = build_payload(spec, route, req, staged)
        assert endpoint
        if route.map.prompt and req.prompt:
            assert payload, "payload is empty"


def test_every_model_has_a_smoke_example():
    missing = [m.id for m in REG.models.values() if not any(e.smoke for e in m.examples)]
    assert not missing, f"models without a smoke example: {missing}"


def test_every_choice_a_model_offers_has_a_route_that_takes_it():
    """A choice no route accepts can only fail with "no route can run" (Kling 3.0 listed 720p; its route renders
    1080p only), so the model must not offer it."""
    unroutable = []
    for spec in REG.models.values():
        routes = [r for r in spec.routes if r.provider != MOCK_PROVIDER]
        if not routes:
            continue
        for p in spec.parameters:
            for option in getattr(p, "options", None) or []:
                if not any("options" not in (rule := r.constraints.get(p.name, {}))
                           or str(option) in map(str, rule["options"]) for r in routes):
                    unroutable.append(f"{spec.id}.{p.name}={option}")
    assert unroutable == []
