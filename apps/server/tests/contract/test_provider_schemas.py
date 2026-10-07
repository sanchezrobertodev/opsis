"""Routes against the provider schemas saved in registry/provider-schemas/ (offline; refresh them with
`uv run python scripts/fetch_provider_schemas.py --update` after reviewing its report)."""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.registry import drift
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())
ROOT = get_settings().resolve_registry_dir() / "provider-schemas"
TARGETS = drift.targets_for(REG)
PUBLIC = [k for k, uses in TARGETS.items() if not drift.SOURCES[uses[0][3].provider].keys]


@pytest.mark.parametrize("key", PUBLIC)
def test_every_public_endpoint_has_a_baseline(key):
    target = TARGETS[key][0][3]
    assert drift.load_baseline(ROOT, target) is not None, (
        f"no saved schema for {key}: run scripts/fetch_provider_schemas.py --update")


@pytest.mark.parametrize("key", sorted(TARGETS))
def test_routes_send_what_the_saved_schema_takes(key):
    uses = TARGETS[key]
    baseline = drift.load_baseline(ROOT, uses[0][3])
    if baseline is None:
        pytest.skip("no baseline (the schema needs a key to read)")
    source = drift.SOURCES[uses[0][3].provider]
    problems = [f.problem for spec, route, mode, t in uses
                for f in drift.check_route(spec, route, mode, baseline, t, source.checks_output)]
    assert problems == []
