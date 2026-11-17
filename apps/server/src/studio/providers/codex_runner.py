"""Runs Codex for the worker, in a container that sees nothing of the studio's (infra/server's `opsis-codex`):
`python -m studio.providers.codex_runner`, with `CODEX_BIN` and `CODEX_HOME` set and the worker's Codex job folder
(`data/provider_results/codex`) mounted where the worker sees it.

Codex keeps a tool that can read files whatever it's told (see `codex.py`), so it runs where the only files are its
own home and the jobs: no `.env`, keys, media, backups or database. The worker parks each job with a `job.json`; the
runner takes it (renaming that file is the claim) and runs it as the worker would without a runner
(`BackgroundJobAdapter._run`: the `running` heartbeat, the `cancel` file, the crop, `meta.json`, `output-N.png`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import anyio

from studio.config import get_settings
from studio.providers.codex import CodexAdapter, check_login, clear_session, session_id
from studio.providers.codex_login import CANCEL, REQUEST, runner_login, take_flag
from studio.providers.common import write_json
from studio.providers.local_tools import STALE_S

log = logging.getLogger("studio.codex_runner")

LOGIN_EVERY_S = 60.0


def claim(adapter: CodexAdapter, job: Path) -> dict[str, Any] | None:
    """Take a job the worker parked and still waits for; None when there's none, it's taken or it was given up on."""
    if job.is_symlink() or not job.is_dir():
        return None
    try:
        os.rename(job / "job.json", job / "job.claimed")
    except OSError:  # no job here, or taken already
        return None
    try:
        given_up = (job / "cancel").exists() or time.time() - (job / "running").lstat().st_mtime > STALE_S
    except OSError:
        given_up = True
    if given_up:  # the take moved on (or was canceled) before it got here
        adapter.store.discard(job.name)
        return None
    payload = json.loads((job / "job.claimed").read_text(encoding="utf-8"))
    (job / "running").touch()
    return payload


def requeue(root: Path) -> None:
    """On start: jobs a runner was drawing when it stopped are drawn again (it's free) while the worker still waits."""
    for job in root.iterdir():
        if job.is_dir() and not job.is_symlink() and (job / "job.claimed").is_file() and not (job / "meta.json").exists():
            clear_session(session_id(job / "codex.log"))
            os.replace(job / "job.claimed", job / "job.json")


async def serve(adapter: CodexAdapter, stop: asyncio.Event | None = None) -> None:
    root = adapter.store.root
    root.mkdir(parents=True, exist_ok=True)
    requeue(root)
    stop = stop or asyncio.Event()
    login, checked = "", float("-inf")
    signing_in = runner_login(root, adapter.command)  # Settings' "Sign in with ChatGPT"
    while not stop.is_set():
        if take_flag(root / REQUEST):
            signing_in.start()
        if take_flag(root / CANCEL):
            signing_in.cancel()
        if signing_in.signed_in.is_set():  # say so in the heartbeat now, not within the minute
            signing_in.signed_in.clear()
            checked = float("-inf")
        if time.monotonic() - checked > LOGIN_EVERY_S:
            login, checked = await anyio.to_thread.run_sync(check_login, adapter.command()), time.monotonic()
        write_json(root / "runner.json", {"login": login})
        for job in sorted(root.iterdir()):
            try:
                payload = claim(adapter, job)
            except (OSError, ValueError):  # a folder it can't read: the take moves on once its heartbeat is stale
                log.exception("can't take %s", job.name)
                continue
            if payload is None:
                continue
            # asked again before each picture, so a switch to an API key never draws on the minute-old answer
            login, checked = await anyio.to_thread.run_sync(check_login, adapter.command()), time.monotonic()
            if login != "chatgpt":  # signed out, or an API key that bills per picture outside the studio's caps
                adapter.store.write_meta(job.name, {"status": "failed", "class": "route_unavailable",
                                                    "error": "Codex in the runner isn't signed in with ChatGPT"})
                (job / "running").unlink(missing_ok=True)
                continue
            log.info("drawing %s", job.name)
            adapter.launch(job.name, payload)
        try:
            await asyncio.wait_for(stop.wait(), 1)
        except TimeoutError:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not get_settings().codex_bin:  # Codex isn't set up: nothing to run, and no reason to be restarted
        log.info("CODEX_BIN isn't set; the Codex runner stays off")
        return
    adapter = CodexAdapter()
    log.info("Codex runner on %s", adapter.store.root)
    asyncio.run(serve(adapter))


if __name__ == "__main__":
    main()
