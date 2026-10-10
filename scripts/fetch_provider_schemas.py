"""Weekly provider-schema drift check: compares what each route sends with what its provider publishes now.

Reads only schema documents (fal per-endpoint OpenAPI, Runway's and ElevenLabs' OpenAPI, World Labs' docs pages;
Replicate when its key is set). Never submits a job, so it costs nothing.

    uv run python scripts/fetch_provider_schemas.py                  # check, write the report
    uv run python scripts/fetch_provider_schemas.py --provider fal   # one provider
    uv run python scripts/fetch_provider_schemas.py --update         # accept what's published now as the baseline

The report goes to data/reports/schema-drift-<date>.md. Baselines live in registry/provider-schemas/ and are what
the contract tests check routes against offline; after reviewing a change, fix the route's YAML and run --update.
Exit code 1 means something needs a look; 0 means nothing changed that affects a route.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "server" / "src"))

from studio.config import get_settings  # noqa: E402
from studio.registry import drift  # noqa: E402
from studio.registry.loader import load_registry  # noqa: E402

BASELINES = ROOT / "registry" / "provider-schemas"
REPORTS = ROOT / "data" / "reports"


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


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", action="append", help="only this provider (repeatable)")
    ap.add_argument("--update", action="store_true", help="write what's published now as the new baselines")
    ap.add_argument("--report", type=Path, help="where to write the report (default data/reports/...)")
    args = ap.parse_args()

    settings = get_settings()
    reg = load_registry(settings.resolve_registry_dir())
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": "ai-media-studio schema check"}) as client:
        report = await drift.run(reg, fetcher(client, asyncio.Semaphore(6)), settings, BASELINES,
                                 providers=set(args.provider or []) or None, update=args.update)
    day = date.today().isoformat()
    text = drift.render(report, day)
    out = args.report or REPORTS / f"schema-drift-{day}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Report: {out}")
    return 1 if report.needs_a_look else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
