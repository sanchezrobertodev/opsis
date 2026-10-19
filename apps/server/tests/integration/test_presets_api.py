"""The presets gallery API: list, render with answers and media, then run the result as a normal generation."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from studio.api.main import app as api


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def test_gallery_lists_presets_with_what_they_need(client):
    r = await client.get("/presets")
    assert r.status_code == 200
    by_id = {p["id"]: p for p in r.json()}
    crash = by_id["crash-zoom-reveal"]
    assert crash["model_name"] == "Kling 3.0" and crash["inputs"][0]["role"] == "start_image"
    assert crash["moves"][0]["label"] == "Crash zoom in" and crash["live_price_usd"] > 0
    local = by_id["through-the-keyhole"]
    assert local["local"] is True and local["live_price_usd"] is None


async def test_render_then_generate(client):
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (90, 30, 30)).save(buf, "PNG")
    up = await client.post("/uploads", files={"file": ("me.png", buf.getvalue(), "image/png")})
    asset_id = up.json()["id"]

    missing = await client.post("/presets/crash-zoom-reveal/render", json={"fields": {"subject": "a cyclist"}})
    assert missing.status_code == 422 and missing.json()["errors"] == ["add Your photo"]

    r = await client.post("/presets/crash-zoom-reveal/render", json={
        "fields": {"subject": "a cyclist"}, "media": [{"role": "start_image", "asset_id": asset_id}]})
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["prompt"].startswith("a cyclist turns sharply toward the camera")
    assert req["direction"]["camera_moves"][0]["id"] == "crash_zoom_in"

    gen = await client.post("/generations", json={**req, "route_policy": "cheapest_healthy"})
    assert gen.status_code == 201, gen.text
    body = gen.json()
    assert "crash zoom" in body["request"]["compiled_prompt"].lower()
    assert body["model_id"] == "kling-3.0" and body["mode"] == "i2v"


async def test_unknown_preset_is_404(client):
    assert (await client.post("/presets/nope/render", json={})).status_code == 404


async def test_template_renders_a_run_that_starts(client):
    r = await client.get("/templates")
    assert r.status_code == 200
    explainer = {t["id"]: t for t in r.json()}["explainer"]
    assert explainer["recipe_name"] and explainer["fields"][0]["key"] == "topic"

    missing = await client.post("/templates/explainer/render", json={})
    assert missing.status_code == 422 and missing.json()["errors"] == ["What to explain is required"]

    r = await client.post("/templates/explainer/render", json={"fields": {"topic": "why the sky is blue"}})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["recipe"] == "faceless" and run["input"]["topic"] == "why the sky is blue"
    assert run["input"]["theme"] == "education" and run["input"]["notes"] == ""

    started = await client.post("/pipelines", json=run)
    assert started.status_code == 201, started.text
    assert started.json()["recipe"] == "faceless"
    assert (await client.post("/templates/nope/render", json={})).status_code == 404
