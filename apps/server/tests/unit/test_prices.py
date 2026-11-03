"""Price freshness: the lint's age warnings, and the weekly price check on hand-made price lists (never a live call)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from studio.budget.reconcile import RouteBill, bills_text
from studio.config import get_settings
from studio.registry import prices
from studio.registry.lint import PRICE_MAX_AGE_DAYS, lint
from studio.registry.loader import Registry, load_registry
from studio.registry.schema import ModelSpec, ProviderSpec

OLD = date.today() - timedelta(days=PRICE_MAX_AGE_DAYS + 5)
NEW = date.today() - timedelta(days=2)


def route(rid: str, provider: str, endpoint: str, pricing: dict[str, Any], **extra) -> dict[str, Any]:
    return {"id": rid, "provider": provider, "modes": {"t2v": {"endpoint": endpoint}},
            "pricing": {"verified": NEW.isoformat(), "source": "x", **pricing},
            "output": {"path": "video.url", "kind": "video"}, **extra}


def model(mid: str, routes: list[dict[str, Any]], params: list[dict[str, Any]] | None = None) -> ModelSpec:
    return ModelSpec.model_validate({
        "id": mid, "name": mid, "family": mid, "vendor": "toy", "output_type": "video", "prompt": {"max_chars": 500},
        "parameters": params or [], "modes": [{"id": "t2v", "label": "Text", "ops": ["t2v"]}], "routes": routes})


def provider(pid: str, adapter: str | None = None, **extra) -> ProviderSpec:
    return ProviderSpec(id=pid, name=pid, kind="aggregator", adapter=adapter or pid, **extra)


# ---------------------------------------------------------------- lint


def test_lint_warns_when_a_price_was_checked_long_ago():
    old = {"basis": "per_output_second", "rates": [{"usd": 0.1}], "verified": OLD.isoformat()}
    spec = model("toy", [route("toy@fal", "fal", "toy/t2v", old),
                         route("toy@fresh", "fal", "toy/t2v", {"basis": "per_request", "rates": [{"usd": 0.1}]}),
                         route("toy@off", "fal", "toy/t2v", old, enabled=False),
                         route("toy@tools", "local_tools", "trim", {**old, "rates": [{"usd": 0}]})])  # free
    reg = Registry(models={"toy": spec}, hash="t", providers={
        "fal": provider("fal"), "local_tools": provider("local_tools"),
        "gpu": provider("gpu", "runpod_comfy", gpu_usd_per_hour={"H100": 4.79}, gpu_verified=OLD),
        "undated": provider("undated", "runpod_comfy", gpu_usd_per_hour={"H100": 4.79})})
    errors, warnings = lint(reg)
    assert errors == []
    stale = [w for w in warnings if "last checked" in w or "gpu_verified" in w]
    assert stale == [f"model toy route toy@fal: price last checked {OLD} ({PRICE_MAX_AGE_DAYS + 5} days ago)",
                     f"provider gpu: GPU prices last checked {OLD} ({PRICE_MAX_AGE_DAYS + 5} days ago)",
                     "provider undated: gpu_usd_per_hour has no `gpu_verified` date"]


def test_the_real_gpu_prices_are_dated():
    reg = load_registry(get_settings().resolve_registry_dir())
    assert reg.providers["runpod_wan_core"].gpu_verified is not None


# ---------------------------------------------------------------- the price check

RESOLUTION = [{"name": "resolution", "type": "enum", "options": ["480p", "720p"], "default": "720p"}]
TIER = [{"name": "tier", "type": "enum", "options": ["standard", "pro"], "default": "standard"}]

SPECS = [
    # one rate: fully checked
    model("solo", [route("solo@fal", "fal", "toy/solo", {"basis": "per_output_second", "rates": [{"usd": 0.1}]})]),
    # an endpoint per tier: each tier's price is checked against its own rate
    model("tiers", [route("tiers@fal", "fal", "toy/{tier}/t2v", {"basis": "per_output_second", "rates": [
        {"when": {"tier": "standard"}, "usd": 0.126}, {"when": {"tier": "pro"}, "usd": 0.168}]},
        map={"params": {"tier": {"to": None}}})], TIER),
    # priced by resolution, one base price listed: only "check by hand" when it matches none of the rates
    model("sizes", [route("sizes@fal", "fal", "toy/sizes", {"basis": "per_output_second", "rates": [
        {"when": {"resolution": "480p"}, "usd": 0.05}, {"when": {"resolution": "720p"}, "usd": 0.08}]})], RESOLUTION),
    model("pixels", [route("pixels@fal", "fal", "toy/pixels", {"basis": "per_image", "rates": [{"usd": 0.03}]})]),
    model("gone", [route("gone@fal", "fal", "toy/gone", {"basis": "per_request", "rates": [{"usd": 0.2}]})]),
    # a formula: Pika's listed numbers must appear in it; the 1080p tier is one this model never sends
    model("seed", [route("seed@pika", "pika", "vendor/seed/t2v", {
        "basis": "formula", "formula": "ceil(21600 * duration) * (6.72 if input_video_seconds > 0 else 11.235) / 1000000"})],
        RESOLUTION),
    model("live", [route("live@reactor", "reactor", "reactor/fast-h3-omni",
                         {"basis": "per_gpu_second", "quantity": "duration", "rates": [{"usd": 0.0125}]})]),
    model("veo", [route("veo@google", "google", "veo-3", {"basis": "per_output_second", "rates": [{"usd": 0.4}],
                                                         "verified": OLD.isoformat()})]),
]
PROVIDERS = {p.id: p for p in [
    provider("fal"), provider("pika"), provider("reactor"), provider("google"),
    provider("wan_core", "runpod_comfy", gpu_usd_per_hour={"RTX PRO 6000": 3.49, "H100": 4.79}, gpu_verified=OLD)]}

FAL = {"toy/solo": (0.12, "second"), "toy/standard/t2v": (0.126, "second"), "toy/pro/t2v": (0.2, "second"),
       "toy/sizes": (0.06, "second"), "toy/pixels": (0.012, "megapixels")}
PIKA = {"apis": [{"api_id": "vendor/seed/t2v", "display_pricing": {"components": [{
    "role": "output", "unit": {"type": "video_output_token", "quantity": 1000000, "included": 0},
    "price_tiers": [{"spec": {"resolution": "480p", "mode": "standard"}, "sell_usd": "11.235"},
                    {"spec": {"resolution": "720p", "variant": "with-video-input"}, "sell_usd": "6.72"},
                    {"spec": {"resolution": "1080p", "mode": "standard"}, "sell_usd": "12.285"}]}]}},
    {"api_id": "vendor/other", "display_pricing": {"components": []}}]}
REACTOR = {"models": [{"name": "fast-h3-omni", "rate": {"amount_per_sec_usd": "0.0125", "currency_code": "USD",
                                                       "denomination": "second"}}]}
GPUS = {"gpus": [{"id": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "name": "RTX PRO 6000",
                  "price": {"secure": 1.89, "community": 1.69, "serverless": 3.79}},
                 {"id": "NVIDIA H100 80GB HBM3", "name": "H100 SXM", "price": {"secure": 2.69, "serverless": 4.79}},
                 {"id": "NVIDIA H100 PCIe", "name": "H100 PCIe", "price": {"secure": 2.39, "serverless": 4.18}},
                 {"id": "NVIDIA A40", "name": "A40", "price": {"secure": 0.4}}]}


def feeds(calls: list[tuple[str, dict[str, str]]]):
    async def fetch(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        calls.append((url, headers))
        parts = urlsplit(url)
        if url.startswith(prices.FalFeed.URL):
            ids = parse_qs(parts.query)["endpoint_id"][0].split(",")
            return 200, {"prices": [{"endpoint_id": e, "unit_price": FAL[e][0], "unit": FAL[e][1], "currency": "USD"}
                                    for e in ids if e in FAL], "next_cursor": None, "has_more": False}
        return 200, {prices.PikaFeed.URL: PIKA, prices.ReactorFeed.URL: REACTOR, prices.RUNPOD_GPUS: GPUS}[url]
    return fetch


async def test_the_price_check_compares_each_route_with_its_providers_list():
    reg = Registry(models={s.id: s for s in SPECS}, providers=PROVIDERS, hash="t")
    calls: list[tuple[str, dict[str, str]]] = []
    settings = SimpleNamespace(fal_key="fal-secret", runpod_api_key="rp-secret")
    report = await prices.run(reg, feeds(calls), settings)

    changed = {x.what: x.problem for x in report.changed}
    check = {x.what: x.problem for x in report.check}
    assert changed == {
        "solo@fal (toy/solo)": "the YAML says $0.1 per second, fal lists $0.12 per second",
        "tiers@fal (toy/pro/t2v)": "the YAML says $0.168 per second, fal lists $0.2 per second",
        "wan_core RTX PRO 6000": "the YAML says $3.49/h, RunPod lists $3.79/h",
    }
    assert check == {
        "sizes@fal (toy/sizes)": "fal lists $0.06 per second, which matches none of the route's rates ($0.05, $0.08)",
        "pixels@fal (toy/pixels)": "fal lists $0.012 per megapixels; the route is priced per image",
        "gone@fal (toy/gone)": "not in fal's price list",
    }
    # standard tier, both Pika tiers (1080p skipped), Reactor and the H100 (one of RunPod's H100s)
    assert report.matched == 5 and report.needs_a_look
    assert report.by_hand == [f"google: `veo@google` {OLD}"]
    fal_calls = [h for u, h in calls if u.startswith(prices.FalFeed.URL)]
    assert len(fal_calls) == 1 and fal_calls[0] == {"Authorization": "Key fal-secret"}
    assert ("https://api.runpod.io/v2/catalog/gpus", {"Authorization": "Bearer rp-secret"}) in calls

    text = prices.render(report)
    assert "## Prices that changed" in text and "## Check by hand" in text and "never writes it" in text
    assert "secret" not in text


async def test_feeds_that_need_a_key_are_skipped_and_one_unknown_id_doesnt_sink_a_batch():
    reg = Registry(models={s.id: s for s in SPECS}, providers=PROVIDERS, hash="t")
    report = await prices.run(reg, feeds([]), SimpleNamespace(fal_key="", runpod_api_key=""), providers={"fal"})
    assert report.unchecked == ["fal: needs FAL_KEY to read its prices"] and not report.by_hand

    async def picky(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        ids = parse_qs(urlsplit(url).query)["endpoint_id"][0].split(",")
        if "toy/gone" in ids:
            return 404, {"error": {"type": "not_found"}}
        return 200, {"prices": [{"endpoint_id": e, "unit_price": 0.12, "unit": "second", "currency": "USD"} for e in ids]}

    listed = await prices.FalFeed().prices(["toy/gone", "toy/solo"], picky, SimpleNamespace(fal_key="k"))
    assert list(listed) == ["toy/solo"]
    # units are matched in the singular, and a minute is 60 seconds
    assert prices.price(1.2, "Minutes", prices.FalFeed.UNITS) == prices.Price(1.2, "Minutes", "per_output_second", 0.02)


async def test_the_real_registry_runs_through_the_check():
    """Every enabled route on a real provider is either compared or listed for a person (fixture feeds)."""
    reg = load_registry(get_settings().resolve_registry_dir())

    async def fetch(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        if url.startswith(prices.FalFeed.URL):
            return 200, {"prices": []}
        catalog = {"apis": [{"api_id": e, "display_pricing": PIKA["apis"][0]["display_pricing"]} for e in
                            ("bytedance/seedance-2.5/text-to-video", "bytedance/seedance-2.5/image-to-video",
                             "bytedance/seedance-2.5/reference-to-video")]}
        return 200, {prices.PikaFeed.URL: catalog, prices.ReactorFeed.URL: REACTOR, prices.RUNPOD_GPUS: GPUS}[url]

    report = await prices.run(reg, fetch, SimpleNamespace(fal_key="k", runpod_api_key="k"))
    assert not [x for x in report.changed + report.check if "@pika" in x.what or "@reactor" in x.what]
    assert all("not in fal's price list" in x.problem for x in report.check if "@fal" in x.what)
    hand = {line.split(":")[0] for line in report.by_hand}
    assert {"google", "elevenlabs", "runway", "worldlabs", "topview"} <= hand
    assert not hand & {"fal", "pika", "reactor", "mock", "comfy_local", "local_tools", "runpod_wan_core", "codex"}


def test_the_bills_section_puts_the_furthest_off_first():
    rows = [RouteBill("ltx-2.5@wan_core", 12, 1.80, 1.62), RouteBill("kling-3.0@fal", 3, 1.20, 1.80),
            RouteBill("sam-3-video@fal", 1, 0.0, 0.42)]
    text = bills_text(rows, 7)
    lines = [line for line in text.splitlines() if line.startswith("| `")]
    assert lines == ["| `sam-3-video@fal` | 1 | $0.00 | $0.42 | no estimate |",
                     "| `kling-3.0@fal` | 3 | $1.20 | $1.80 | 1.50 |",
                     "| `ltx-2.5@wan_core` | 12 | $1.80 | $1.62 | 0.90 |"]
    assert "No take was corrected" in bills_text([], 7)
