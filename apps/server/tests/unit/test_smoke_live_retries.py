"""The live smoke script rides out a restarting local API (a code change under --reload) instead of crashing mid-run,
and a retried submit reuses its Idempotency-Key, so the API hands back the same generation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("smoke_live", ROOT / "scripts" / "smoke_live.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)


def test_dropped_connections_are_tried_again_with_the_same_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Idempotency-Key"))
        if len(seen) < 3:
            raise httpx.ReadError("[WinError 10054] An existing connection was forcibly closed by the remote host")
        return httpx.Response(201, json={"id": "g1"})

    api = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    r = smoke.call(api, "POST", "/generations", json={}, headers={"Idempotency-Key": "smoke-x-1"})
    assert r.status_code == 201 and seen == ["smoke-x-1"] * 3


def test_an_api_that_stays_away_still_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        smoke.call(api, "GET", "/generations/g1")


def test_an_upload_is_never_replayed_after_its_answer_may_be_lost():
    calls = []

    def lost(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadError("[WinError 10054] reset after the upload was sent")

    api = httpx.Client(base_url="http://api", transport=httpx.MockTransport(lost))
    with pytest.raises(httpx.ReadError):
        smoke.call(api, "POST", "/uploads", replay=False, files={"file": ("a.png", b"x")})
    assert calls == ["/uploads"]  # once: a second try could leave a duplicate asset

    tries = []

    def refused_then_up(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        if len(tries) == 1:
            raise httpx.ConnectError("refused")  # the API was restarting: nothing reached it
        return httpx.Response(201, json={"id": "a1"})

    api = httpx.Client(base_url="http://api", transport=httpx.MockTransport(refused_then_up))
    assert smoke.call(api, "POST", "/uploads", replay=False, files={"file": ("a.png", b"x")}).json() == {"id": "a1"}


def test_each_test_file_is_uploaded_once_per_run(monkeypatch):
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(1)
        return httpx.Response(201, json={"id": f"a{len(posts)}"})

    monkeypatch.setattr(smoke, "_uploaded", {})
    api = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    first = smoke.upload_fixture(api, "fixture:images/keyframe.png")
    assert smoke.upload_fixture(api, "fixture:images/keyframe.png") == first and len(posts) == 1
