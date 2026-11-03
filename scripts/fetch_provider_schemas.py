"""Weekly provider check: what each route sends against the schema its provider publishes now, its price against
the provider's price list, and its estimates against the bills.

Reads only schema documents (fal per-endpoint OpenAPI, Runway's and ElevenLabs' OpenAPI, World Labs' docs pages;
Replicate when its key is set), price lists (fal's pricing API, Pika's catalog, Reactor's /pricing, RunPod's GPU
catalog; `studio/registry/prices.py`) and the spend ledger. Never submits a job, so it costs nothing, and never
writes a route's price: a person reviews the report and fixes the YAML.

    uv run python scripts/fetch_provider_schemas.py                  # check, write the report
    uv run python scripts/fetch_provider_schemas.py --provider fal   # one provider
    uv run python scripts/fetch_provider_schemas.py --update         # accept what's published now as the baseline

The report goes to data/reports/schema-drift-<date>.md. Baselines live in registry/provider-schemas/ and are what
the contract tests check routes against offline; after reviewing a change, fix the route's YAML and run --update.
The owner's keys saved in Settings > Keys are used when the database is up; else .env's.
Exit code 1 means something needs a look (a schema or a price); 0 means nothing changed that affects a route.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.budget.reconcile import RouteBill, bills_text, by_route  # noqa: E402
from studio.config import get_settings  # noqa: E402
from studio.registry import drift, prices  # noqa: E402
from studio.registry.loader import load_registry  # noqa: E402

BASELINES = ROOT / "registry" / "provider-schemas"
REPORTS = ROOT / "data" / "reports"
BILL_DAYS = 7


def fetcher(client: httpx.AsyncClient, limit: asyncio.Semaphore) -> drift.Fetch:
    cache: dict[str, asyncio.Task[tuple[int, Any]]] = {}

    async def get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        async with limit:
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                return 0, {"error": repr(exc)}
        try:
            return r.status_code, r.json()
        except ValueError:  # a docs page (World Labs publishes its OpenAPI inside one) comes back as text
            text = r.headers.get("content-type", "").startswith(("text/plain", "text/markdown"))
            return r.status_code, r.text if text else None

    async def fetch(url: str, headers: dict[str, str]) -> tuple[int, Any]:
        # several routes share one document (Runway, ElevenLabs): read it once
        if url not in cache:
            cache[url] = asyncio.ensure_future(get(url, headers))
        return await cache[url]

    return fetch


async def from_database() -> tuple[list[RouteBill] | None, str]:
    """Apply the owner's saved keys and read each route's estimates against its bills; or why the database couldn't
    be read (the check then runs on .env's keys)."""
    from studio import keys
    from studio.db.session import get_engine, session_scope

    try:
        async with session_scope() as s:
            await keys.load(s)
            return await by_route(s, datetime.now(UTC) - timedelta(days=BILL_DAYS)), ""
    except (OSError, SQLAlchemyError) as exc:
        return None, f"Couldn't read the database ({type(exc).__name__}): saved keys and bills left out.\n"
    finally:
        await get_engine().dispose()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", action="append", help="only this provider (repeatable)")
    ap.add_argument("--update", action="store_true", help="write what's published now as the new baselines")
    ap.add_argument("--report", type=Path, help="where to write the report (default data/reports/...)")
    args = ap.parse_args()

    settings = get_settings()
    reg = load_registry(settings.resolve_registry_dir())
    bills, db_problem = await from_database()
    only = set(args.provider or []) or None
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": "opsis schema check"}) as client:
        fetch = fetcher(client, asyncio.Semaphore(6))
        report = await drift.run(reg, fetch, settings, BASELINES, providers=only, update=args.update)
        price_report = await prices.run(reg, fetch, settings, providers=only)
    day = date.today().isoformat()
    text = "\n".join([drift.render(report, day), prices.render(price_report),
                      bills_text(bills, BILL_DAYS) if bills is not None else "", db_problem]).rstrip() + "\n"
    out = args.report or REPORTS / f"schema-drift-{day}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Report: {out}")
    return 1 if report.needs_a_look or price_report.needs_a_look else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
