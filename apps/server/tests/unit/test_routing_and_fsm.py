from datetime import UTC, datetime, timedelta

from studio.domain.enums import TERMINAL_STATUSES, GenStatus
from studio.domain.fsm import ALLOWED, can_transition
from studio.registry.loader import Registry, mock_route
from studio.registry.routing import RouteHealth, RoutingEnv, candidates, cheapest_live_estimate
from studio.registry.schema import ModelSpec, ProviderSpec
from studio.registry.validate import normalize


def make_registry() -> tuple[Registry, ModelSpec]:
    spec = ModelSpec.model_validate({
        "id": "m", "name": "M", "family": "m", "vendor": "v", "output_type": "video",
        "parameters": [{"name": "duration", "type": "int", "min": 1, "max": 20, "default": 5},
                       {"name": "audio", "type": "bool", "default": False}],
        "modes": [{"id": "t2v", "label": "T2V"}, {"id": "edit", "label": "Edit"}],
        "routes": [
            {"id": "m@cheap", "provider": "a", "modes": {"t2v": {"endpoint": "e"}},
             "constraints": {"duration": {"max": 10}},
             "pricing": {"basis": "per_output_second", "rates": [{"usd": 0.1}]}},
            {"id": "m@pricey", "provider": "b", "modes": {"t2v": {"endpoint": "e"}, "edit": {"endpoint": "e"}},
             "unsupported_params": ["audio"],
             "pricing": {"basis": "per_output_second", "rates": [{"usd": 0.3}]}},
        ],
    })
    spec.routes.append(mock_route(spec))
    providers = {
        "a": ProviderSpec(id="a", name="A", kind="aggregator", adapter="a", key_setting="fal_key"),
        "b": ProviderSpec(id="b", name="B", kind="direct", adapter="b", key_setting="gemini_api_key"),
        "mock": ProviderSpec(id="mock", name="Mock", kind="mock", adapter="mock"),
    }
    return Registry(models={"m": spec}, providers=providers, hash="h"), spec


def env(mode="live", allow=(), keys=("a", "b"), adapters=("a", "b", "mock"), health=None) -> RoutingEnv:
    return RoutingEnv(provider_mode=mode, live_allowlist=set(allow), has_key=lambda p: p in keys,
                      has_adapter=lambda a: a in adapters, health=health or {})


def eligible(cands):
    return [c.route.id for c in cands if c.eligible]


def test_mock_mode_only_mock_but_reports_live_reference():
    reg, spec = make_registry()
    cands = candidates(reg, spec, normalize(spec, "t2v", "x", {}, []), env(mode="mock"))
    assert eligible(cands) == ["m@mock"]
    ref = cheapest_live_estimate(cands)
    assert ref is not None and ref.route.id == "m@cheap"


def test_live_mode_sorts_by_cost_and_excludes_mock():
    reg, spec = make_registry()
    cands = candidates(reg, spec, normalize(spec, "t2v", "x", {}, []), env())
    assert eligible(cands) == ["m@cheap", "m@pricey"]


def test_mixed_mode_allowlist():
    reg, spec = make_registry()
    req = normalize(spec, "t2v", "x", {}, [])
    assert eligible(candidates(reg, spec, req, env(mode="mixed"))) == ["m@mock"]
    assert eligible(candidates(reg, spec, req, env(mode="mixed", allow=["m@pricey"]))) == ["m@pricey"]


def test_missing_key_adapter_breaker_constraints_and_unsupported_params():
    reg, spec = make_registry()
    req = normalize(spec, "t2v", "x", {}, [])
    assert eligible(candidates(reg, spec, req, env(keys=("b",)))) == ["m@pricey"]
    assert eligible(candidates(reg, spec, req, env(adapters=("b",)))) == ["m@pricey"]
    open_breaker = {"m@cheap": RouteHealth(breaker_open_until=datetime.now(UTC) + timedelta(minutes=5))}
    assert eligible(candidates(reg, spec, req, env(health=open_breaker))) == ["m@pricey"]
    long_req = normalize(spec, "t2v", "x", {"duration": 15}, [])
    assert eligible(candidates(reg, spec, long_req, env())) == ["m@pricey"]
    audio_req = normalize(spec, "t2v", "x", {"audio": True}, [])
    assert eligible(candidates(reg, spec, audio_req, env())) == ["m@cheap"]


def test_mode_support_and_pinning():
    reg, spec = make_registry()
    edit = normalize(spec, "edit", "x", {}, [])
    assert eligible(candidates(reg, spec, edit, env())) == ["m@pricey"]
    t2v = normalize(spec, "t2v", "x", {}, [])
    assert eligible(candidates(reg, spec, t2v, env(), policy="pinned:m@pricey")) == ["m@pricey"]


def test_fsm_terminal_states_have_no_exits():
    for status in TERMINAL_STATUSES:
        assert ALLOWED[status] == frozenset()


def test_fsm_every_state_reachable_from_draft_and_can_end():
    seen, frontier = {GenStatus.draft}, [GenStatus.draft]
    while frontier:
        for nxt in ALLOWED[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(GenStatus)
    for status in GenStatus:
        if status not in TERMINAL_STATUSES:
            assert ALLOWED[status] & TERMINAL_STATUSES, f"{status} can never finish"


def test_fsm_key_rules():
    assert can_transition(GenStatus.submitting, GenStatus.unknown)
    assert not can_transition(GenStatus.unknown, GenStatus.queued)  # never auto-resubmit ambiguous work
    assert not can_transition(GenStatus.ingesting, GenStatus.canceled)  # output already paid for
    assert not can_transition(GenStatus.succeeded, GenStatus.queued)
