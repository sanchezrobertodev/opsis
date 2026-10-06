"""API launcher: `python -m studio.api [--reload]`.

On Windows psycopg (the queue driver) needs a SelectorEventLoop; uvicorn's default there is Proactor.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    src = Path(__file__).resolve().parents[2]
    uvicorn.run(
        "studio.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(src)] if args.reload else None,
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
        # Browsers hold the SSE stream open forever; without a cap a --reload restart waits on it indefinitely.
        timeout_graceful_shutdown=3,
        log_level="info",
    )


if __name__ == "__main__":
    main()
