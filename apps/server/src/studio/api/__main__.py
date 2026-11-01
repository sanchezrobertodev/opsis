"""API launcher: `python -m studio.api [--reload]`.

On Windows psycopg (the queue driver) needs a SelectorEventLoop; uvicorn's default there is Proactor.
With --reload every saved source file restarts the server, dropping requests in flight; /health reports it
(`reloads_on_change`) so long live runs can warn. Run without it for those.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from studio.config import get_settings

RELOAD_ENV = "STUDIO_API_RELOAD"


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=get_settings().api_port)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    src = Path(__file__).resolve().parents[2]
    os.environ[RELOAD_ENV] = "1" if args.reload else ""  # the server process inherits it
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
