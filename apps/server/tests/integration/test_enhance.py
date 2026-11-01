"""Claude prompt enhancement: mock by default; in live mode a fake client stands in for the API.

Nothing here reaches Anthropic. Live-mode tests check the request we send (model, structured output,
refusal fallback) and that every paid call lands in the spend ledger, including ones that fail.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.config import get_settings
from studio.db.models import SpendLedger
from studio.llm import client as llm_client
from studio.llm import enhance as enhance_mod
from studio.llm.enhance import EnhancedPrompt, build_request
from studio.llm.pricing import cost
from studio.registry.loader import get_registry


@pytest.fixture
async def client(db, in_memory_queue):
    enhance_mod._cache.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_response(*, text="A red kite climbs over the bay, tail ribbons snapping", stop="end_turn",
                  model="claude-sonnet-5-5", parsed=True):
    return SimpleNamespace(
        model=model, stop_reason=stop, stop_details=SimpleNamespace(category="general_harms") if stop == "refusal" else None,
        usage=SimpleNamespace(input_tokens=900, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        parsed_output=EnhancedPrompt(prompt=text, changes=["Added motion beats"]) if parsed else None,
        _request_id="req_test",
    )


@pytest.fixture
def live(monkeypatch):
    def install(response):
        messages = FakeMessages(response)
        monkeypatch.setattr(get_settings(), "llm_mode", "live")
        monkeypatch.setattr(llm_client, "get_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
        return messages
    return install


async def ledger():
    from studio.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        return (await s.execute(select(SpendLedger).where(SpendLedger.provider == "anthropic"))).scalars().all()


def test_request_carries_dialect_direction_and_mention_rules():
    from studio.director.compiler import ShotDirection

    veo = get_registry().model("veo-3.1")
    text = build_request(veo, "i2v", "@maya runs", ShotDirection.model_validate(
        {"camera_moves": [{"id": "dolly_in"}], "lighting": "golden_hour"}))
    assert "Flowing, concrete sentences" in text and "can generate sound" in text
    assert "starts from the user's image" in text
    assert "camera movement" in text and "lighting and colour palette" in text
    assert text.endswith("User's idea:\n@maya runs")


def test_token_pricing():
    assert cost("claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=0) == Decimal("2")
    assert cost("claude-opus-5-5", input_tokens=0, output_tokens=1_000_000) == Decimal("20")
    assert cost("claude-sonnet-5-5", input_tokens=0, output_tokens=0, cache_read=1_000_000) == Decimal("0.2")


async def test_calls_name_the_model_and_a_typical_price(client):
    r = await client.get("/llm/calls")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "mock"  # free now; the prices are what it would cost live
    assert body["calls"]["enhance"] == {"model": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5",
                                        "usd": float(cost("claude-sonnet-5-5", input_tokens=1_500, output_tokens=800))}
    assert body["calls"]["cinema.director"]["name"] == "Claude Opus 5.5"
    assert all(0 < c["usd"] < 1 for c in body["calls"].values())


async def test_mock_mode_is_free_and_keeps_mentions(client):
    r = await client.post("/llm/enhance", json={"model_id": "kling-3.0", "mode": "t2v", "prompt": "@juno runs"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["cost_usd"] == 0
    assert body["prompt"].startswith("@juno runs,") and body["warnings"] == []


async def test_live_call_is_structured_charged_and_cached(client, live):
    messages = live(fake_response())
    before = len(await ledger())
    payload = {"model_id": "seedance-2.5", "mode": "t2v", "prompt": "a kite over the bay"}
    r = await client.post("/llm/enhance", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["prompt"].startswith("A red kite climbs") and r.json()["mock"] is False

    call = messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["output_format"] is EnhancedPrompt
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "low"}

    rows = await ledger()
    assert len(rows) == before + 1
    row = max(rows, key=lambda e: e.created_at)
    assert row.amount_usd == cost("claude-sonnet-5-5", input_tokens=900, output_tokens=300)
    assert row.basis["purpose"] == "enhance" and row.route_id == "anthropic/claude-sonnet-5-5"

    again = await client.post("/llm/enhance", json=payload)
    assert again.json()["prompt"] == r.json()["prompt"] and len(messages.calls) == 1  # cached, not re-billed
    assert len(await ledger()) == before + 1
    history = (await client.get("/budget/history")).json()["entries"]
    assert history[0]["kind"] == "claude" and history[0]["label"] == "Claude: enhance"


async def test_dropped_mentions_are_flagged(client, live):
    live(fake_response(text="A fox runs through tall grass"))
    r = await client.post("/llm/enhance", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "@juno runs"})
    assert r.json()["warnings"] == ["@juno was dropped; add it back if you need it"]


async def test_refusal_is_reported_and_still_billed(client, live):
    live(fake_response(stop="refusal", parsed=False))
    before = len(await ledger())
    r = await client.post("/llm/enhance", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "something"})
    assert r.status_code == 502 and "declined" in r.json()["detail"]
    assert len(await ledger()) == before + 1


async def test_spending_switch_blocks_live_calls(client, live):
    messages = live(fake_response())
    assert (await client.patch("/budget", json={"live_enabled": False})).status_code == 200
    try:
        r = await client.post("/llm/enhance", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "x"})
        assert r.status_code == 402 and messages.calls == []
    finally:
        await client.patch("/budget", json={"live_enabled": True})


async def test_missing_key_explains_itself(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_mode", "live")
    monkeypatch.setattr(llm_client, "_client", None)
    r = await client.post("/llm/enhance", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "x"})
    assert r.status_code == 503 and "Settings > Keys" in r.json()["detail"]
