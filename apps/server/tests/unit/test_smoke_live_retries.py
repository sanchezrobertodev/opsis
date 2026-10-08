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
