"""Worker entrypoint: `python -m studio.worker`.

psycopg's async driver can't run on Windows' default ProactorEventLoop, so the worker runs on a
SelectorEventLoop. That loop can't spawn asyncio subprocesses, which is why ffmpeg runs through
subprocess.run on threads (studio.media.ffmpeg).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from studio import keys
from studio.registry.loader import get_registry
from studio.worker.app import app


async def _main(concurrency: int, queues: list[str] | None) -> None:
    reg = get_registry()  # fail fast on a broken registry
    logging.getLogger("studio").info("registry %s loaded: %d models", reg.hash, len(reg.models))
    key_refresh = asyncio.create_task(keys.refresh_forever(), name="key-refresh")  # keys saved in Settings
    try:
        async with app.open_async():
            await app.run_worker_async(
                queues=queues,
                concurrency=concurrency,
                name="studio-worker",
                delete_jobs="successful",
            )
    finally:
        key_refresh.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await key_refresh


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio.worker")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--queues", default="", help="comma-separated queue names (default: all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("procrastinate").setLevel(logging.WARNING)
    queues = [q for q in args.queues.split(",") if q] or None
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        asyncio.run(_main(args.concurrency, queues), loop_factory=loop_factory)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
