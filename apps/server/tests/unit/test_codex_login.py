"""Signing Codex in with ChatGPT from Settings, with a stand-in `codex` that prints what `login --device-auth` prints
(colored, as the real one does) and signs in once the test approves."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

FAKE = r'''
import os, sys, time
from pathlib import Path
home = Path(os.environ["CODEX_HOME"])
args = sys.argv[1:]
if args[:2] == ["login", "status"]:
    print((home / "auth.txt").read_text() if (home / "auth.txt").exists() else "Not logged in")
    sys.exit(0)
if args[:2] == ["login", "--device-auth"]:
    print("Follow these steps to sign in with ChatGPT using device code authorization:\n")
    print("1. Open this link in your browser and sign in to your account\n   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n")
    print("2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n   \x1b[94mBIT6-GAMYL\x1b[0m", flush=True)
    for _ in range(200):
        if (home / "approved").exists():
            (home / "auth.txt").write_text("Logged in using ChatGPT")
            sys.exit(0)
        time.sleep(0.05)
    sys.exit(1)
'''


@pytest.fixture
def fake(monkeypatch, tmp_path: Path):
    from studio.config import get_settings

    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE, encoding="utf-8")
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(get_settings(), "media_root", tmp_path / "media")
    monkeypatch.setattr(get_settings(), "codex_bin", "codex")
    return [sys.executable, str(script)], home


def wait_for(check, seconds: float = 10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (got := check()) is not None:
            return got
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_the_link_and_code_are_shown_and_it_ends_signed_in(fake):
    from studio.providers.codex_login import DeviceLogin

    binary, home = fake
    states: list[dict] = []
    login = DeviceLogin(lambda: binary, states.append)
    login.start()
    waiting = wait_for(lambda: next((s for s in states if s["state"] == "waiting"), None))
    assert waiting["link"] == "https://auth.openai.com/codex/device" and waiting["code"] == "BIT6-GAMYL"
    assert waiting["expires_at"] > time.time() + 14 * 60
    (home / "approved").touch()
    wait_for(lambda: True if states[-1]["state"] == "signed_in" else None)
    assert login.signed_in.is_set() and [s["state"] for s in states] == ["starting", "waiting", "signed_in"]


def test_a_canceled_sign_in_says_so(fake):
    from studio.providers.codex_login import DeviceLogin

    binary, _ = fake
    states: list[dict] = []
    login = DeviceLogin(lambda: binary, states.append)
    login.start()
    wait_for(lambda: True if states[-1]["state"] == "waiting" else None)
    login.cancel()
    wait_for(lambda: True if states[-1]["state"] == "canceled" else None)
    assert not login.signed_in.is_set()


def test_only_a_link_to_openais_sign_in_page_reaches_settings():
    from studio.providers.codex_login import clean

    assert clean({"state": "waiting", "link": "javascript:alert(1)", "code": "BIT6-GAMYL"})["link"] is None
    assert clean({"state": "waiting", "link": "https://auth.openai.com.evil.example/x", "code": "x"}) == {
        "state": "waiting", "link": None, "code": None, "expires_at": None, "detail": None}


async def test_on_a_server_the_runner_signs_in_when_settings_asks(fake, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.api.deps import current_user
    from studio.api.routers import codex as router
    from studio.config import get_settings
    from studio.providers.codex import CodexAdapter, runner_state
    from studio.providers.codex_runner import serve

    binary, home = fake
    monkeypatch.setattr(get_settings(), "codex_runner", True)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[current_user] = lambda: None
    client = TestClient(app)
    assert client.post("/codex/login").status_code == 503  # no runner yet

    drawer = CodexAdapter()
    monkeypatch.setattr(drawer, "command", lambda: binary)
    stop = asyncio.Event()
    task = asyncio.create_task(serve(drawer, stop))

    async def until(check):
        for _ in range(200):
            if got := check():
                return got
            await asyncio.sleep(0.05)
        raise AssertionError("timed out")

    try:
        await until(lambda: runner_state(drawer.store.root))
        assert runner_state(drawer.store.root)["login"] == "signed_out"
        assert client.post("/codex/login").json()["state"] == "starting"
        shown = await until(lambda: (s := client.get("/codex/login").json())["state"] == "waiting" and s)
        assert shown["code"] == "BIT6-GAMYL" and shown["link"] == "https://auth.openai.com/codex/device"
        (home / "approved").touch()
        await until(lambda: client.get("/codex/login").json()["state"] == "signed_in")
        # the heartbeat says so at once, so routing and Settings use Codex without waiting a minute
        await until(lambda: runner_state(drawer.store.root)["login"] == "chatgpt")
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)
