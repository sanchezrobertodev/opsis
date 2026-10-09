"""Training a character's identity on the mock provider: photos -> zip -> trainer -> the LoRA on the Element ->
Wan 2.2 loads it whenever the character is mentioned."""

from __future__ import annotations

import io
import uuid
import zipfile

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Asset, Element, Generation
from studio.db.session import session_scope
from studio.pipelines.recipes.lora_train import default_trigger
from studio.storage.local import get_storage


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def _character(client, photos: int) -> tuple[dict, list[str]]:
    ids = []
    for i in range(photos):
        buf = io.BytesIO()
        Image.new("RGB", (256, 320), (20 + i * 9, 80, 140)).save(buf, "PNG")
        up = (await client.post("/uploads", files={"file": (f"p{i}.png", buf.getvalue(), "image/png")})).json()
        ids.append(up["id"])
    el = (await client.post("/elements", json={
        "name": f"Maya {uuid.uuid4().hex[:6]}", "description": "a lighthouse keeper with short grey hair",
        "images": [{"asset_id": a, "view": "front"} for a in ids]})).json()
    return el, ids


async def test_a_trained_identity_lands_on_the_character_and_loads_when_mentioned(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, get, start  # importable once the suite is collected

    el, photos = await _character(client, 10)
    trigger = default_trigger(el["slug"], el["id"])
    run = await start(client, "lora_train", element_id=el["id"], slug=el["slug"], photos=photos, trigger=trigger,
                      steps=600)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert list(steps) == ["dataset", "train", "attach", "preview"]

    # the photos went to the trainer as one zip
    async with session_scope() as s:
        archive = await s.get(Asset, uuid.UUID(steps["dataset"]["output"]["asset_id"]))
        train = await s.get(Generation, uuid.UUID(steps["train"]["generation_id"]))
    assert archive.kind == "archive" and archive.mime == "application/zip"
    with zipfile.ZipFile(get_storage().path_for(archive.storage_key)) as z:
        assert sorted(z.namelist()) == [f"image_{i:03d}.png" for i in range(1, 11)]
    assert train.model_id == "wan-2.2-lora-trainer"
    assert train.request["params"] == {"trigger_phrase": trigger, "steps": 600, "face_crop": False}

    # the character now carries both experts' LoRA files
    lora = (await client.get(f"/elements/{el['id']}")).json()["lora"]
    assert lora["base"] == "wan-2.2-a14b" and lora["trigger"] == trigger and lora["high"] and lora["low"]
    async with session_scope() as s:
        files = {str(a.id): a for a in (await s.execute(select(Asset).where(
            Asset.id.in_([uuid.UUID(lora["low"]), uuid.UUID(lora["high"])])))).scalars()}
    assert {a.kind for a in files.values()} == {"lora"}
    assert files[lora["low"]].metadata_["expert"] == "low" and files[lora["high"]].metadata_["expert"] == "high"

    # the preview mentioned the character, so Wan 2.2 got its LoRA and its trigger word
    async with session_scope() as s:
        preview = await s.get(Generation, uuid.UUID(steps["preview"]["generation_id"]))
    assert preview.model_id == "wan-2.2-image"
    assert {(m["role"], m["asset_id"]) for m in preview.request["media"]} == {("lora", lora["low"]),
                                                                              ("lora_high", lora["high"])}
    assert f"({trigger})" in preview.compiled["prompt"] and "@" not in preview.compiled["prompt"]
    assert done["output"]["asset_id"] and done["output"]["trigger"] == trigger

    # with a second character whose LoRA is one file, each character's two experts still stay together
    other, _ = await _character(client, 0)
    async with session_scope() as s:
        row = await s.get(Element, uuid.UUID(other["id"]))
        row.lora = {"base": "wan-2.2-a14b", "trigger": "solozqtest", "low": lora["high"], "high": None}
    out = await client.post("/generations", json={"model_id": "wan-2.2-image", "mode": "t2i",
                                                  "prompt": f"@{other['slug']} and @{el['slug']} on a pier"})
    assert out.status_code == 201, out.text
    media = out.json()["request"]["media"]
    assert [m["asset_id"] for m in media if m["role"] == "lora"] == [lora["low"], lora["high"]]
    assert [m["asset_id"] for m in media if m["role"] == "lora_high"] == [lora["high"]]

    # forgetting the identity puts the character back to words
    cleared = (await client.patch(f"/elements/{el['id']}", json={"clear_lora": True})).json()
    assert cleared["lora"] is None


async def test_a_missing_character_stops_the_run_before_the_paid_trainer(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, get, start

    el, photos = await _character(client, 10)
    await client.delete(f"/elements/{el['id']}")
    run = await start(client, "lora_train", element_id=el["id"], slug=el["slug"], photos=photos)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "failed" and "character is gone" in (done["error"] or "")
    async with session_scope() as s:
        trainers = (await s.execute(select(Generation).where(
            Generation.group_id == uuid.UUID(run["id"])))).scalars().all()
    assert trainers == []


async def test_training_needs_enough_photos_and_a_usable_trigger(client):
    el, photos = await _character(client, 3)
    r = await client.post("/pipelines", json={"recipe": "lora_train", "input": {
        "element_id": el["id"], "slug": el["slug"], "photos": photos, "trigger": "Maya!"}})
    assert r.status_code == 422
    errors = " ".join(r.json()["errors"])
    assert "10 to 80 photos" in errors and "trigger word" in errors
