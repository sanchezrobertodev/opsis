"""Show a file in this computer's file manager ("Show in folder").

Only useful when the browser runs on the machine the API runs on: anywhere else the folder would open on the
server, or nowhere (in a container).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import Request

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def opener(path: Path) -> list[str] | str | None:
    """The command that shows `path` selected in its folder; None when this machine has no file manager to call."""
    if sys.platform == "win32":
        # explorer reads `/select,"path"` itself and misreads it quoted whole; a Windows path can't hold a quote
        return f'explorer /select,"{path}"'
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    xdg = shutil.which("xdg-open")
    return [xdg, str(path.parent)] if xdg else None


def can_reveal(request: Request) -> bool:
    """A browser on this machine (not through a proxy), outside a container, with a file manager to call."""
    host = request.client.host if request.client else None
    forwarded = "x-forwarded-for" in request.headers or "forwarded" in request.headers
    return host in LOOPBACK and not forwarded and not in_container() and opener(Path.cwd()) is not None


def reveal(path: Path) -> None:
    command = opener(path)
    if command is None:
        raise RuntimeError("no file manager to show the file in")
    # never through a shell: the path is one argument
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
