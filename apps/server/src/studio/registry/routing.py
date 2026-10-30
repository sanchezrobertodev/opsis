"""Route selection: which vendor runs a request, and in what failover order.

A route is the same model weights through a different vendor, so failover never changes quality.
Pure function of (spec, request, environment, health) to keep it unit-testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from studio.registry.loader import MOCK_PROVIDER, Registry
from studio.registry.pricing import CostEstimate, estimate, eta_seconds
from studio.registry.schema import ModelSpec, Route
from studio.registry.validate import NormalizedRequest

ProviderMode = Literal["mock", "mixed", "live"]


@dataclass
class RouteHealth:
    breaker_open_until: datetime | None = None
    success_24h: int = 0
    failure_24h: int = 0
    p50_latency_ms: int | None = None

    @property
    def failure_rate(self) -> float:
        total = self.success_24h + self.failure_24h
        return self.failure_24h / total if total else 0.0

    def breaker_open(self, now: datetime) -> bool:
        return self.breaker_open_until is not None and self.breaker_open_until > now


@dataclass
class RoutingEnv:
    provider_mode: ProviderMode
    live_allowlist: set[str]
    has_key: Callable[[str], bool]  # provider id -> key configured?
    has_adapter: Callable[[str], bool]  # adapter key -> implemented?
    health: dict[str, RouteHealth] = field(default_factory=dict)
    reachable: Callable[[str], bool] = lambda _provider: True  # local engines: is the server up?
    # local engines that are up: why one can't run this route's mode (missing model files, too little GPU memory)
    blocked: Callable[[Route, str], str | None] = lambda _route, _mode: None
    setting_set: Callable[[str], bool] = lambda _name: True  # is this Settings field filled in?
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Candidate:
    route: Route
    estimate: CostEstimate | None
    eligible: bool
    reason: str | None = None
    provider_name: str | None = None
    provider_kind: str | None = None
    eta_seconds: float | None = None
    account: str | None = None  # the billing account (ProviderSpec.billing_account, else the provider id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route.id,
            "provider": self.route.provider,
            "provider_name": self.provider_name or self.route.provider,
            "provider_kind": self.provider_kind,
            "account": self.account or self.route.provider,
            "eta_seconds": self.eta_seconds,
            "eligible": self.eligible,
            "reason": self.reason,
            "estimate_usd": float(self.estimate.usd) if self.estimate else None,
            "pricing_basis": self.estimate.basis if self.estimate else None,
            "is_mock": self.route.provider == MOCK_PROVIDER,
        }


def _constraint_violation(route: Route, req: NormalizedRequest) -> str | None:
    for name, rule in route.constraints.items():
        if name.startswith("media."):
            role = name.removeprefix("media.")
            count = sum(1 for m in req.media if m.role == role)
            if "max" in rule and count > rule["max"]:
                return f"accepts at most {rule['max']} {role}"
            continue
        value = req.params.get(name)
        if value is None:
            continue
        if "options" in rule and value not in rule["options"] and str(value) not in map(str, rule["options"]):
            return f"{name}={value} not offered on this route ({rule['options']})"
        if "min" in rule and value < rule["min"]:
            return f"{name} below route minimum {rule['min']}"
        if "max" in rule and value > rule["max"]:
            return f"{name} above route maximum {rule['max']}"
    return None


def _mode_gate(
    route: Route, kind: str | None, env: RoutingEnv, model_has_allowlisted: bool, local_first: bool
) -> str | None:
    is_mock = route.provider == MOCK_PROVIDER
    if kind == "local":
        return None  # free and offline: allowed in every provider mode
    if is_mock and local_first:
        # A real local engine runs this model; never swap its output for a test pattern.
        return "this model runs on the local engine"
    if env.provider_mode == "mock":
        return None if is_mock else "PROVIDER_MODE=mock (live routes disabled)"
    if env.provider_mode == "live":
        return "mock routes are off in live mode" if is_mock else None
    # mixed: allowlisted live routes run live; everything else stays mocked.
    if model_has_allowlisted:
        return None if route.id in env.live_allowlist else "not in LIVE_ROUTE_ALLOWLIST"
    return None if is_mock else "not in LIVE_ROUTE_ALLOWLIST"


def _local_ready(reg: Registry, r: Route, mode: str, env: RoutingEnv) -> bool:
    """A local route for this mode whose engine is set up (not necessarily running)."""
    p = reg.providers.get(r.provider)
    return bool(
        p and p.kind == "local" and r.enabled and mode in r.modes and env.has_adapter(p.adapter)
        and (not p.key_setting or env.has_key(r.provider))
    )


def local_only(reg: Registry, spec: ModelSpec) -> bool:
    """Every real route runs on a local engine (Wan 2.2 5B, FLUX.2 klein 4B): there's nowhere else to run it."""
    kinds = [p.kind for r in spec.routes if r.provider != MOCK_PROVIDER and (p := reg.providers.get(r.provider))]
    return bool(kinds) and all(k == "local" for k in kinds)


def runs_locally(reg: Registry, spec: ModelSpec, mode: str, env: RoutingEnv) -> bool:
    """A local engine is set up, running, and able to run this mode here (its model files, enough GPU memory)."""
    return any(_local_ready(reg, r, mode, env) and env.reachable(r.provider) and env.blocked(r, mode) is None
               for r in spec.routes)


def candidates(
    reg: Registry,
    spec: ModelSpec,
    req: NormalizedRequest,
    env: RoutingEnv,
    policy: str = "cheapest_healthy",
) -> list[Candidate]:
    """Every route for the model with eligibility, reason and estimate. Eligible ones first, in order."""
    model_has_allowlisted = any(r.id in env.live_allowlist for r in spec.routes)
    # The mock gives way to a local engine that can run this, and to a set-up one for a model that runs nowhere else
    # (so an engine that's off says so instead of drawing test patterns). A model that also runs on RunPod or a hosted
    # API keeps its mock while the local engine can't run it.
    local_first = runs_locally(reg, spec, req.mode, env) or (
        any(_local_ready(reg, r, req.mode, env) for r in spec.routes) and local_only(reg, spec))
    out: list[Candidate] = []
    for route in spec.routes:
        est: CostEstimate | None = None
        reason: str | None = None
        provider = reg.providers.get(route.provider)
        if req.mode not in route.modes:
            reason = f"does not support mode {req.mode}"
        else:
            try:
                est = estimate(route.pricing, req.params, req.prompt, req.media, req.mode)
            except ValueError as exc:
                reason = f"pricing error: {exc}"
        if reason is None and not route.enabled:
            reason = "disabled in registry"
        if reason is None and route.sunset and env.now.date() >= route.sunset:
            reason = f"retired by the provider on {route.sunset.isoformat()}"
        if reason is None and provider is None:
            reason = f"unknown provider {route.provider}"
        kind = provider.kind if provider else None
        if reason is None:
            reason = _mode_gate(route, kind, env, model_has_allowlisted, local_first)
        if reason is None and provider and not env.has_adapter(provider.adapter):
            reason = f"{provider.name} adapter not implemented yet"
        if reason is None and provider and provider.key_setting and not env.has_key(route.provider):
            reason = (f"{provider.name} isn't configured" if kind == "local" else f"no API key for {provider.name}")
        if reason is None and provider and provider.hosting_setting and not env.setting_set(provider.hosting_setting):
            hosted = sorted({m.kind for m in req.media if m.kind in provider.hosted_kinds})
            if hosted:
                reason = (f"{provider.name} takes {' and '.join(hosted)} inputs only as web links; set "
                          f"{provider.hosting_setting.upper()} so they can be hosted")
        if reason is None and kind == "local" and not env.reachable(route.provider):
            reason = f"{provider.name} isn't running. Start it, then try again."
        if reason is None and kind == "local":
            reason = env.blocked(route, req.mode)
        if reason is None:
            health = env.health.get(route.id)
            if health and health.breaker_open(env.now):
                reason = "circuit breaker open"
        if reason is None:
            reason = _constraint_violation(route, req)
        if reason is None:
            for name in route.unsupported_params:
                param = spec.param(name)
                if name in req.params and param is not None and req.params[name] != param.default:
                    reason = f"does not support {name}"
                    break
        out.append(Candidate(route=route, estimate=est, eligible=reason is None, reason=reason,
                             provider_name=provider.name if provider else None, provider_kind=kind,
                             account=(provider.billing_account or provider.id) if provider else None,
                             eta_seconds=eta_seconds(route.eta, req.params, req.media, req.mode) if est else None))

    def sort_key(c: Candidate) -> tuple:
        h = env.health.get(c.route.id) or RouteHealth()
        usd = float(c.estimate.usd) if c.estimate else float("inf")
        if policy == "fastest":
            return (not c.eligible, h.p50_latency_ms or 10**9, usd)
        return (not c.eligible, usd, h.failure_rate, h.p50_latency_ms or 10**9)

    out.sort(key=sort_key)
    if policy.startswith("pinned:"):
        pinned = policy.removeprefix("pinned:")
        for c in out:
            if c.route.id != pinned and c.eligible:
                c.eligible = False
                c.reason = f"route pinned to {pinned}"
        out.sort(key=sort_key)
    return out


def cheapest_live_estimate(cands: list[Candidate]) -> Candidate | None:
    """What this would cost on the cheapest real route, regardless of keys/adapters. Shown next to mock runs."""
    # estimate is None when the route lacks the requested mode, so this keeps only comparable routes; a free local
    # route that can't run here (no model files) isn't a price this machine would pay
    live = [c for c in cands if c.route.provider != MOCK_PROVIDER and c.estimate is not None and c.route.enabled
            and (c.provider_kind != "local" or c.eligible)]
    return min(live, key=lambda c: c.estimate.usd) if live else None
