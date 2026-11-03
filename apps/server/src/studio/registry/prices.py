"""Price check: do the routes' YAML prices still match what the providers list now?

Each provider with a price list has a `Feed` that reads it and returns `Price`s per endpoint: fal's pricing API (one
base price per endpoint, any key), Pika's catalog (a price per option: resolution, mode, with or without a video
input) and Reactor's `/pricing` (its session rate). A listed price is compared with the route's YAML:
- on the route's own basis: with the rate for the options it names (a tier, the `{param}` an endpoint is templated
  on, the one mode that calls the endpoint), else with any of the route's rates, multiplied or not. A single-rate route is fully checked; one
  priced by resolution, audio or tier is flagged "check by hand" only when the base price matches none of its rates;
- a formula route: the listed number has to appear in the formula (Seedance on Pika: $11.235 per M tokens);
- a unit the route isn't priced by: check by hand.
Our RunPod endpoint's `gpu_usd_per_hour` is compared with RunPod's GPU catalog (serverless USD per GPU-hour).
Providers without a price list are listed with each route's `verified` date. Only price lists are read: nothing here
costs anything, and nothing writes the YAML (a person fixes it and moves `verified`).
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlencode

from studio.registry import drift
from studio.registry.loader import Registry
from studio.registry.mapping import endpoint_for
from studio.registry.schema import EnumParam, ModelSpec, Route

TOLERANCE = 0.005  # relative: a listed price within half a percent of the YAML's is the same price
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
FAL_BATCH = 50  # endpoint ids per pricing call (fal's limit)
RUNPOD_GPUS = "https://api.runpod.io/v2/catalog/gpus"

# a provider's unit -> (the route pricing basis it compares with, how many of that basis' units one of it is)
Units = dict[str, tuple[str, float]]
BASIS_UNIT = {"per_output_second": "second", "per_image": "image", "per_request": "request",
              "per_1k_chars": "1,000 characters", "per_video_token": "video token", "per_gpu_second": "GPU second"}


@dataclass
class Price:
    """One listed price: `listed` USD per `unit` (the provider's words) for the options in `spec`. `usd` is the same
    price per one unit of `basis`, the route pricing basis it compares with (None: no basis does)."""

    listed: float
    unit: str
    basis: str | None
    usd: float
    spec: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        options = ", ".join(f"{k} {v}" for k, v in self.spec.items())
        return f"${self.listed:g} per {self.unit}" + (f" ({options})" if options else "")


def price(listed: float, unit: str, units: Units, *, quantity: float = 1, key: str | None = None,
          spec: dict[str, Any] | None = None) -> Price:
    """A listed price per `quantity` of a unit, its basis looked up in a feed's table by `key` (default `unit`)."""
    basis, n = units.get(re.sub(r"s\b", "", (key or unit).strip().lower()), (None, 1.0))
    return Price(listed, unit, basis, listed / quantity / n, spec or {})


# ---------------------------------------------------------------- where each provider lists its prices


class Feed:
    keys: tuple[str, ...] = ()  # settings needed to read the prices

    async def prices(self, endpoints: list[str], fetch: drift.Fetch, settings: Any) -> dict[str, list[Price]]:
        raise NotImplementedError


class FalFeed(Feed):
    """fal's pricing API: one base price per endpoint (unit_price, unit, currency)."""

    keys = ("fal_key",)
    URL = "https://api.fal.ai/v1/models/pricing"
    UNITS: Units = {"second": ("per_output_second", 1), "minute": ("per_output_second", 60),
                    "image": ("per_image", 1), "video": ("per_request", 1), "request": ("per_request", 1),
                    "generation": ("per_request", 1), "1000 character": ("per_1k_chars", 1),
                    "1k character": ("per_1k_chars", 1), "compute second": ("per_gpu_second", 1)}

    async def prices(self, endpoints: list[str], fetch: drift.Fetch, settings: Any) -> dict[str, list[Price]]:
        out: dict[str, list[Price]] = {}
        batches = [endpoints[i:i + FAL_BATCH] for i in range(0, len(endpoints), FAL_BATCH)]
        while batches:
            batch = batches.pop(0)
            status, body = await fetch(f"{self.URL}?{urlencode({'endpoint_id': ','.join(batch)})}",
                                       {"Authorization": f"Key {settings.fal_key}"})
            if status == 404 and len(batch) > 1:  # fal doesn't say whether one unknown id fails the whole call
                batches += [[e] for e in batch]
                continue
            if status == 404:
                continue  # not in fal's price list: reported by the caller
            if status != 200 or not isinstance(body, dict):
                raise drift.Unavailable(f"fal pricing returned {status}")
            for p in body.get("prices") or []:
                currency = p.get("currency") or "USD"
                unit = str(p.get("unit") or "unit") + ("" if currency == "USD" else f" ({currency})")
                listed = price(float(p["unit_price"]), unit, self.UNITS if currency == "USD" else {})
                out.setdefault(p["endpoint_id"], []).append(listed)
        return out


class PikaFeed(Feed):
    """Pika's public catalog: each API's price tiers by option (`display_pricing.components[].price_tiers[]`)."""

    URL = "https://api.dev.pika.art/catalog/apis"
    UNITS: Units = {"second": ("per_output_second", 1), "minute": ("per_output_second", 60),
                    "image": ("per_image", 1), "request": ("per_request", 1), "character": ("per_1k_chars", 0.001)}

    async def prices(self, endpoints: list[str], fetch: drift.Fetch, settings: Any) -> dict[str, list[Price]]:
        status, body = await fetch(self.URL, {})
        if status != 200 or not isinstance(body, dict):
            raise drift.Unavailable(f"Pika catalog returned {status}")
        out: dict[str, list[Price]] = {}
        for api in body.get("apis") or []:
            if api.get("api_id") not in endpoints:
                continue
            for c in (api.get("display_pricing") or {}).get("components") or []:
                unit = c.get("unit") or {}
                kind, n = str(unit.get("type") or "unit"), float(unit.get("quantity") or 1)
                label = kind.replace("_", " ") if n == 1 else f"{n:,.0f} {kind.replace('_', ' ')}"
                for tier in c.get("price_tiers") or []:
                    out.setdefault(api["api_id"], []).append(
                        price(float(tier["sell_usd"]), label, self.UNITS, quantity=n, key=kind, spec=tier.get("spec")))
        return out


class ReactorFeed(Feed):
    """Reactor's `/pricing`: a per-second session rate per model (what its adapter checks before every session)."""

    URL = "https://api.reactor.inc/pricing"

    async def prices(self, endpoints: list[str], fetch: drift.Fetch, settings: Any) -> dict[str, list[Price]]:
        status, body = await fetch(self.URL, {})
        if status != 200 or not isinstance(body, dict):
            raise drift.Unavailable(f"Reactor pricing returned {status}")
        by_name = {e.rsplit("/", 1)[-1]: e for e in endpoints}  # route endpoint reactor/<model name>
        out: dict[str, list[Price]] = {}
        for m in body.get("models") or []:
            rate = m.get("rate") or {}
            if m.get("name") in by_name and rate.get("currency_code") == "USD":
                unit = str(rate.get("denomination") or "second")
                out[by_name[m["name"]]] = [price(float(rate["amount_per_sec_usd"]), unit,
                                                 {"second": ("per_gpu_second", 1)})]
        return out


FEEDS: dict[str, Feed] = {"fal": FalFeed(), "pika": PikaFeed(), "reactor": ReactorFeed()}


# ---------------------------------------------------------------- comparing


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=1e-9)


def options(spec: ModelSpec, route: Route, listed: dict[str, Any]) -> dict[str, Any] | None:
    """A price's options that are this model's choices; None when one is a choice this route can't send (Pika's
    1080p tier for a model offering 480p and 720p). Other keys (Pika's `variant`) aren't the model's and are left out."""
    out = {}
    for key, value in listed.items():
        p = spec.param(key)
        if not isinstance(p, EnumParam):
            continue
        allowed = route.constraints.get(key, {}).get("options", p.options)
        if str(value) not in map(str, allowed):
            return None
        out[key] = value
    return out


def compare(route: Route, p: Price, opts: dict[str, Any], provider: str) -> tuple[str, str] | None:
    """("changed" | "check", what to look at), or None when the YAML agrees with the listed price."""
    pricing = route.pricing
    if pricing.basis == "formula":
        if any(_close(p.listed, float(n)) for n in NUMBER.findall(pricing.formula or "")):
            return None
        return "check", f"{provider} lists {p.label}, a number the formula doesn't contain"
    unit = BASIS_UNIT.get(pricing.basis, pricing.basis)
    if p.basis != pricing.basis:
        return "check", f"{provider} lists {p.label}; the route is priced per {unit}"
    # the rate for these options, most specific first (as pricing picks one), else every rate
    named = [r for r in pricing.rates if r.when and all(str(opts.get(k)) in map(str, v if isinstance(v, list) else [v])
                                                        for k, v in r.when.items())]
    top = max((len(r.when) for r in named), default=0)
    rates = [r for r in named if len(r.when) == top] or pricing.rates
    factors = [1.0, *(m.factor for m in pricing.multipliers)]
    yaml = sorted({round(r.usd / r.per * f, 9) for r in rates for f in factors})
    if any(_close(p.usd, y) for y in yaml):
        return None
    if len(yaml) == 1:
        return "changed", f"the YAML says ${yaml[0]:g} per {unit}, {provider} lists {p.label}"
    return "check", f"{provider} lists {p.label}, which matches none of the route's rates " \
                    f"({', '.join(f'${y:g}' for y in yaml)})"


# ---------------------------------------------------------------- a run over the whole registry


@dataclass
class Line:
    what: str  # a route id, or a provider and GPU
    problem: str
    verified: date | None


@dataclass
class PriceReport:
    changed: list[Line] = field(default_factory=list)  # the YAML's price differs from the listed one
    check: list[Line] = field(default_factory=list)  # can't tell from the listed price alone: a person looks
    unchecked: list[str] = field(default_factory=list)  # couldn't read the prices this time, and why
    by_hand: list[str] = field(default_factory=list)  # providers without a price list, with their routes' dates
    matched: int = 0

    @property
    def needs_a_look(self) -> bool:
        return bool(self.changed or self.check)


def _endpoints(spec: ModelSpec, template: str) -> list[tuple[str, dict[str, Any]]]:
    """The endpoints a route mode calls, with the param values a templated one stands for (`{tier}`)."""
    choices = []
    for name in drift.PLACEHOLDER.findall(template):
        p = spec.param(name)
        if not isinstance(p, EnumParam):
            return [(template, {})]
        choices.append([(name, o) for o in p.options])
    return [(endpoint_for(template, dict(c)), dict(c)) for c in itertools.product(*choices)]


async def run(reg: Registry, fetch: drift.Fetch, settings: Any, *, providers: set[str] | None = None) -> PriceReport:
    report = PriceReport()
    adapter = {pid: p.adapter for pid, p in reg.providers.items()}
    uses: dict[str, dict[str, list[tuple[ModelSpec, Route, dict[str, Any]]]]] = {}
    hand: dict[str, list[Route]] = {}
    for spec in reg.models.values():
        for route in spec.routes:
            if (not route.enabled or (providers and route.provider not in providers)
                    or adapter.get(route.provider, route.provider) in drift.OWN):
                continue
            if route.provider not in FEEDS:
                hand.setdefault(route.provider, []).append(route)
                continue
            called: dict[str, tuple[dict[str, Any], set[str]]] = {}
            for mode_id, rm in route.modes.items():
                for endpoint, opts in _endpoints(spec, rm.endpoint):
                    called.setdefault(endpoint, (opts, set()))[1].add(mode_id)
            for endpoint, (opts, modes) in called.items():
                if len(modes) == 1 and spec.param("mode") is None:  # rates can name the mode (`when: {mode: edit}`)
                    opts = {**opts, "mode": next(iter(modes))}
                uses.setdefault(route.provider, {}).setdefault(endpoint, []).append((spec, route, opts))

    for pid, endpoints in sorted(uses.items()):
        feed = FEEDS[pid]
        if missing := [k for k in feed.keys if not getattr(settings, k, "")]:
            report.unchecked.append(f"{pid}: needs {', '.join(m.upper() for m in missing)} to read its prices")
            continue
        try:
            listed = await feed.prices(sorted(endpoints), fetch, settings)
        except drift.Unavailable as exc:
            report.unchecked.append(f"{pid}: {exc}")
            continue
        for endpoint, routes in sorted(endpoints.items()):
            for spec, route, opts in routes:
                where = f"{route.id} ({endpoint})"
                if not listed.get(endpoint):
                    report.check.append(Line(where, f"not in {pid}'s price list", route.pricing.verified))
                for p in listed.get(endpoint, []):
                    if (named := options(spec, route, p.spec)) is None:
                        continue
                    found = compare(route, p, {**opts, **named}, pid)
                    if found is None:
                        report.matched += 1
                    else:
                        getattr(report, found[0]).append(Line(where, found[1], route.pricing.verified))

    await _gpus(reg, fetch, settings, report, providers)
    for pid, routes in sorted(hand.items()):
        dated = sorted(routes, key=lambda r: r.pricing.verified or date.min)
        report.by_hand.append(f"{pid}: " + ", ".join(f"`{r.id}` {r.pricing.verified}" for r in dated))
    return report


async def _gpus(reg: Registry, fetch: drift.Fetch, settings: Any, report: PriceReport,
                providers: set[str] | None) -> None:
    """Our RunPod endpoints' `gpu_usd_per_hour` against RunPod's catalog (GPUs matched by name, as the adapter does)."""
    priced = [p for p in reg.providers.values() if p.gpu_usd_per_hour and (not providers or p.id in providers)]
    if not priced:
        return
    if not settings.runpod_api_key:
        report.unchecked.append("RunPod GPU prices: needs RUNPOD_API_KEY to read the catalog")
        return
    status, body = await fetch(RUNPOD_GPUS, {"Authorization": f"Bearer {settings.runpod_api_key}"})
    if status != 200 or not isinstance(body, dict):
        report.unchecked.append(f"RunPod GPU catalog returned {status}")
        return
    gpus = [g for g in body.get("gpus") or [] if (g.get("price") or {}).get("serverless") is not None]
    for provider in priced:
        for name, usd in provider.gpu_usd_per_hour.items():
            listed = sorted({float(g["price"]["serverless"]) for g in gpus
                             if name in str(g.get("name", "")) or name in str(g.get("id", ""))})
            where = f"{provider.id} {name}"
            if not listed:
                report.check.append(Line(where, f"RunPod lists no serverless price for a GPU named {name}",
                                         provider.gpu_verified))
            elif any(_close(usd, x) for x in listed):
                report.matched += 1
            else:
                report.changed.append(Line(where, f"the YAML says ${usd:g}/h, RunPod lists "
                                                  f"{' or '.join(f'${x:g}/h' for x in listed)}", provider.gpu_verified))


def render(report: PriceReport) -> str:
    lines = ["# Price check", "", f"{report.matched} listed prices match the YAML. "
             + ("Something needs a look." if report.needs_a_look else "Nothing changed."), ""]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend([f"## {title}", "", *[f"- {r}" for r in rows], ""])

    section("Prices that changed", [f"`{x.what}`: {x.problem} (checked {x.verified})" for x in report.changed])
    section("Check by hand", [f"`{x.what}`: {x.problem} (checked {x.verified})" for x in report.check])
    section("Couldn't check", report.unchecked)
    section("No price list: check by hand (route, last checked)", report.by_hand)
    if report.needs_a_look:
        lines.append("Fix the route's YAML and move its `verified` date; this check never writes it.")
    return "\n".join(lines).rstrip() + "\n"
