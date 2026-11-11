"""Signing Codex in with a ChatGPT plan from Settings. `codex login --device-auth` prints a link and a one-time code;
the person opens the link, signs in and types the code, and the command ends signed in. It runs where Codex runs: in
the API's process when the worker runs Codex itself, or in the Codex runner (infra/server's `opsis-codex`), which the
API asks with a `login.request` flag in the Codex job folder (`login.cancel` stops it) and which answers in
`login.json` there. Nothing the command prints is passed on but the link and the code."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from studio.providers.codex import check_login, codex_env
from studio.providers.common import open_plain, touch_flag, write_json

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LINK = re.compile(r"https://auth\.openai\.com/[\w/.-]+")
CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b")
EXPIRES_S = 15 * 60  # Codex's codes last 15 minutes
REQUEST, CANCEL, STATE = "login.request", "login.cancel", "login.json"


class DeviceLogin:
    """One `codex login --device-auth` at a time; each change of state goes to `write`."""

    def __init__(self, binary: Callable[[], list[str]], write: Callable[[dict[str, Any]], None]):
        self.binary, self.write = binary, write
        self.proc: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.canceled = False
        self.signed_in = threading.Event()  # set when one ends signed in, for a caller that re-checks

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        self.canceled = False
        self.write({"state": "starting"})
        self.thread = threading.Thread(target=self._run, name="codex-login", daemon=True)
        self.thread.start()

    def cancel(self) -> None:
        self.canceled = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _run(self) -> None:
        try:
            self.proc = subprocess.Popen([*self.binary(), "login", "--device-auth"], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=codex_env())
        except OSError as exc:
            self.write({"state": "failed", "detail": f"Codex didn't start ({type(exc).__name__}); check CODEX_BIN"})
            return
        timer = threading.Timer(EXPIRES_S + 30, self.proc.kill)  # the code has expired by then
        timer.start()
        link = code = None
        try:
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                line = ANSI.sub("", raw)
                found = not (link and code)
                link = link or ((m := LINK.search(line)) and m.group(0))
                code = code or ((m := CODE.search(line)) and m.group(0))
                if found and link and code:
                    self.write({"state": "waiting", "link": link, "code": code, "expires_at": time.time() + EXPIRES_S})
            done = self.proc.wait()
        finally:
            timer.cancel()
        if self.canceled:
            self.write({"state": "canceled"})
        elif done == 0 and check_login(self.binary()) == "chatgpt":
            self.write({"state": "signed_in"})
            self.signed_in.set()
        else:
            self.write({"state": "failed", "detail": "the code expired or the sign-in didn't finish" if code
                        else "Codex didn't show a sign-in code"})


def read_state(folder: Path) -> dict[str, Any]:
    """The runner's sign-in as it last wrote it; `starting` while a request waits for it."""
    import json

    try:
        with open_plain(folder / STATE) as f:
            state = json.loads(f.read(16_000))
    except (OSError, ValueError):
        state = None
    if not isinstance(state, dict):
        state = {"state": "starting" if (folder / REQUEST).exists() else "idle"}
    return clean(state)


def clean(state: dict[str, Any]) -> dict[str, Any]:
    """Only what Settings shows, and a link only to OpenAI's sign-in page."""
    out = {k: state.get(k) for k in ("state", "link", "code", "expires_at", "detail")}
    if not isinstance(out["link"], str) or not LINK.fullmatch(out["link"]):
        out["link"] = None
    if not isinstance(out["code"], str) or not CODE.fullmatch(out["code"]):
        out["code"] = None
    return out


def ask_runner(folder: Path) -> None:
    (folder / STATE).unlink(missing_ok=True)  # the last sign-in's answer is no longer the one shown
    touch_flag(folder / REQUEST)


def take_flag(path: Path) -> bool:
    """Whether a flag was there, removing it (unlinking never follows a link left in its place)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def runner_login(folder: Path, binary: Callable[[], list[str]]) -> DeviceLogin:
    return DeviceLogin(binary, lambda state: write_json(folder / STATE, state))
