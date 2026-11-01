"""Provider keys pasted in Settings: encrypted at rest, never read back, and in use at once over .env's."""

from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from studio import keys
from studio.api.main import app as api
from studio.config import get_settings
from studio.db.models import ProviderKey
from studio.db.session import session_scope

FAL = "fal-abc123-secret-9876"


@pytest.fixture
async def client(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c
    async with session_scope() as session:
        await session.execute(delete(ProviderKey))
    keys.apply({})  # back to what .env said, for the other tests


def by_setting(rows: list[dict]) -> dict[str, dict]:
    return {r["setting"]: r for r in rows}


async def test_a_saved_key_is_used_at_once_and_never_shown(client):
    assert all(r["source"] is None for r in (await client.get("/keys")).json())

    r = await client.put("/keys/fal_key", json={"value": f"  {FAL}\n"})
    assert r.status_code == 200, r.text
    assert FAL not in r.text
    row = by_setting(r.json())["fal_key"]
    assert (row["source"], row["last4"], row["unreadable"]) == ("app", "9876", False)
    assert get_settings().fal_key == FAL  # adapters read it as before, no restart
    async with session_scope() as session:
        stored = await session.scalar(select(ProviderKey.ciphertext).where(ProviderKey.setting == "fal_key"))
    assert FAL.encode() not in stored

    r = await client.delete("/keys/fal_key")
    assert by_setting(r.json())["fal_key"]["source"] is None and get_settings().fal_key == ""


async def test_a_saved_key_wins_over_env_and_removing_it_brings_env_back(client, monkeypatch):
    monkeypatch.setitem(keys._from_env, "gemini_api_key", "env-gemini-key-5678")
    keys.apply({})
    assert by_setting((await client.get("/keys")).json())["gemini_api_key"]["source"] == "env"

    await client.put("/keys/gemini_api_key", json={"value": "app-gemini-key-1234"})
    assert get_settings().gemini_api_key == "app-gemini-key-1234"
    row = by_setting((await client.delete("/keys/gemini_api_key")).json())["gemini_api_key"]
    assert (row["source"], row["last4"], get_settings().gemini_api_key) == ("env", "5678", "env-gemini-key-5678")


async def test_a_key_the_secret_cant_read_shows_as_unreadable_and_env_applies(client, monkeypatch):
    await client.put("/keys/fal_key", json={"value": FAL})
    monkeypatch.setattr(get_settings(), "keys_secret", Fernet.generate_key().decode())  # the secret was lost
    row = by_setting((await client.get("/keys")).json())["fal_key"]
    assert (row["unreadable"], row["source"]) == (True, None) and get_settings().fal_key == ""


async def test_refusals(client):
    assert (await client.put("/keys/database_url", json={"value": "postgresql://x"})).status_code == 404
    assert (await client.put("/keys/fal_key", json={"value": "two words"})).status_code == 422
    assert (await client.put("/keys/fal_key", json={"value": ""})).status_code == 422
