"""Capped live smoke test: run each route's cheapest `smoke: true` example through the real API.

Plans first and spends nothing unless --confirm-spend is given; never exceeds --cap (USD, estimated).

    uv run python scripts/smoke_live.py --routes veo-3.1@fal,kling-3.0@fal --cap 3            # dry run
    uv run python scripts/smoke_live.py --routes veo-3.1@fal --cap 3 --confirm-spend         # spends

The API and worker must be running with PROVIDER_MODE=mixed and the routes in LIVE_ROUTE_ALLOWLIST
(or PROVIDER_MODE=live), plus the provider keys in .env. Local routes (comfy_local) are free and always run.
Reports go to smoke_reports/<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.config import get_settings  # noqa: E402
from studio.registry.loader import MOCK_PROVIDER, load_registry  # noqa: E402

TERMINAL = {"succeeded", "failed", "canceled"}


def plan(routes_filter: set[str] | None):
    reg = load_registry(get_settings().resolve_registry_dir())
    items = []
    for spec in reg.models.values():
        for route in spec.routes:
            if route.provider == MOCK_PROVIDER or (routes_filter and route.id not in routes_filter):
                continue
            examples = [e for e in spec.examples if e.smoke and e.mode in route.modes]
            if examples:
                items.append((spec, route, examples[0]))
    missing = (routes_filter or set()) - {r.id for _, r, _ in items}
    return items, sorted(missing)


def upload_fixture(api: httpx.Client, ref: str) -> str:
    path = ROOT / "fixtures" / ref.removeprefix("fixture:")
    with path.open("rb") as fh:
        r = api.post("/uploads", files={"file": (path.name, fh)})
    r.raise_for_status()
    return r.json()["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routes", default="", help="comma-separated route ids (default: every route with a smoke example)")
    ap.add_argument("--cap", type=float, required=True, help="maximum total estimated spend in USD")
    ap.add_argument("--confirm-spend", action="store_true", help="actually submit (otherwise plan only)")
    ap.add_argument("--api", default="http://127.0.0.1:8400")
    ap.add_argument("--timeout", type=int, default=1200, help="seconds to wait per generation")
    args = ap.parse_args()

    routes_filter = {r.strip() for r in args.routes.split(",") if r.strip()} or None
    items, missing = plan(routes_filter)
    if missing:
        print(f"no smoke example for: {', '.join(missing)}")
    api = httpx.Client(base_url=args.api, timeout=60)
    try:
        health = api.get("/health").json()
    except httpx.HTTPError:
        print(f"API not reachable at {args.api}. Start it with `pnpm dev`.")
        return 2
    print(f"provider mode: {health['provider_mode']}")

    total, runs = 0.0, []
    for spec, route, ex in items:
        media = [{"role": role, "asset_id": upload_fixture(api, ref)}
                 for role, refs in ex.media.items() for ref in (refs if isinstance(refs, list) else [refs])]
        body = {"model_id": spec.id, "mode": ex.mode, "prompt": ex.prompt, "params": ex.params, "media": media,
                "route_policy": f"pinned:{route.id}"}
        est = api.post("/registry/estimate", json=body)
        if est.status_code != 200:
            print(f"  skip {route.id}: {est.json().get('errors')}")
            continue
        e = est.json()
        chosen = next((c for c in e["candidates"] if c["route_id"] == route.id), None)
        if e["chosen_route_id"] != route.id:
            print(f"  skip {route.id}: {chosen and chosen['reason']}")
            continue
        usd = e["estimate_usd"] or 0.0
        if total + usd > args.cap:
            print(f"  stop before {route.id}: ${usd:.2f} would exceed the ${args.cap:.2f} cap (planned ${total:.2f})")
            break
        total += usd
        runs.append((route.id, body, usd))
        print(f"  plan {route.id:34s} ${usd:6.3f}  {ex.name}")

    print(f"planned total: ${total:.3f} of ${args.cap:.2f}")
    if not args.confirm_spend:
        print("dry run: add --confirm-spend to submit")
        return 0

    report = []
    for route_id, body, usd in runs:
        started = time.monotonic()
        r = api.post("/generations", json={**body, "confirm_cost_usd": usd},
                     headers={"Idempotency-Key": f"smoke-{route_id}-{datetime.now(UTC):%Y%m%d%H%M}"})
        if r.status_code != 201:
            report.append({"route": route_id, "status": "rejected", "detail": r.json()})
            print(f"  {route_id}: rejected {r.json()}")
            continue
        gen = r.json()
        while gen["status"] not in TERMINAL and time.monotonic() - started < args.timeout:
            time.sleep(5)
            gen = api.get(f"/generations/{gen['id']}").json()
        seconds = round(time.monotonic() - started)
        entry = {"route": route_id, "generation_id": gen["id"], "status": gen["status"], "seconds": seconds,
                 "estimated_usd": usd, "actual_usd": gen.get("actual_cost_usd"), "error": gen.get("error_message"),
                 "assets": [a["url"] for a in gen.get("assets", [])]}
        report.append(entry)
        print(f"  {route_id}: {gen['status']} in {seconds}s" + (f" ({gen['error_message']})" if gen.get("error_message") else ""))

    out = ROOT / "smoke_reports" / f"{datetime.now(UTC):%Y-%m-%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"cap_usd": args.cap, "planned_usd": total, "runs": report}, indent=2))
    print(f"report: {out}")
    return 0 if all(x["status"] == "succeeded" for x in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
