"""The Settings switch between mock and real generation (and Claude): immediate, and never past .env's mode."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import update

from studio.api.main import app as api
from studio.config import get_settings
from studio.db.models import BudgetPolicy
from studio.db.session import session_scope
from studio.llm import client as llm


@pytest.fixture
async def client(db, in_memory_queue, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "provider_mode", "live")  # the ceiling a live studio's .env sets
    monkeypatch.setattr(s, "llm_mode", "live")
    monkeypatch.setattr(s, "fal_key", "test-key")  # a configured provider, so a live route can be chosen
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c
    async with session_scope() as session:  # the policy row is shared: leave .env in charge for other tests
        await session.execute(update(BudgetPolicy).values(provider_mode=None, llm_mode=None))
        await session.commit()


async def route(client) -> str:
    r = await client.post("/registry/estimate", json={
        "model_id": "nano-banana-2", "mode": "t2i", "prompt": "a lamp", "route_policy": "cheapest_healthy"})
    assert r.status_code == 200, r.text
    return r.json()["chosen_route_id"]


async def test_the_switch_takes_effect_at_once_within_the_env_ceiling(client):
    budget = (await client.get("/budget")).json()
    assert (budget["provider_mode"], budget["provider_modes"]) == ("live", ["mock", "mixed", "live"])
    assert (budget["llm_mode"], budget["llm_modes"]) == ("live", ["mock", "live"])
    assert not (await route(client)).endswith("@mock")

    r = await client.patch("/budget", json={"provider_mode": "mock", "llm_mode": "mock"})
    assert r.status_code == 200 and (r.json()["provider_mode"], r.json()["llm_mode"]) == ("mock", "mock")
    assert (await route(client)).endswith("@mock")  # no restart: the next request routes to the mock
    assert (await client.get("/health")).json()["provider_mode"] == "mock"
    assert (await client.get("/registry")).json()["provider_mode"] == "mock"
    async with session_scope() as session:
        assert await llm.is_mock(session, get_settings().owner_user_id)

    r = await client.patch("/budget", json={"provider_mode": "live"})
    assert r.json()["provider_mode"] == "live" and not (await route(client)).endswith("@mock")


async def test_a_mock_only_env_cant_be_switched_live(client, monkeypatch):
    await client.patch("/budget", json={"provider_mode": "live"})  # chosen while .env allowed it
    monkeypatch.setattr(get_settings(), "provider_mode", "mock")  # .env now says mock (a dev PC)
    budget = (await client.get("/budget")).json()
    assert (budget["provider_mode"], budget["provider_modes"]) == ("mock", ["mock"])
    assert (await route(client)).endswith("@mock")
    r = await client.patch("/budget", json={"provider_mode": "live"})
    assert r.status_code == 422 and "PROVIDER_MODE in .env is mock" in r.text
