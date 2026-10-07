"""Characters/locations/props (Elements) and how @mentions reach each kind of model."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from studio.api.main import app as api
from studio.director.elements import mentions, slugify


def test_slugify_and_mention_parsing():
    assert slugify("Maya Ortiz") == "maya-ortiz"
    assert slugify("Café Noir!") == "cafe-noir"
    assert slugify("   ") == "element"
    assert mentions("@maya meets @Old-Town at dusk, email me@x.com, @maya again") == ["maya", "old-town"]


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def upload(client, color=(200, 60, 40)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (512, 512), color).save(buf, "PNG")
    r = await client.post("/uploads", files={"file": ("ref.png", buf.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def make_element(client, name, kind="character", description="", images=1) -> dict:
    ids = [await upload(client, (40 * i, 80, 160)) for i in range(images)]
    r = await client.post("/elements", json={"kind": kind, "name": name, "description": description,
                                             "images": [{"asset_id": i, "view": "front"} for i in ids]})
    assert r.status_code == 201, r.text
    return r.json()


async def test_crud_and_unique_handles(client):
    a = await make_element(client, "Nova Reyes", description="a 30-year-old pilot with a shaved head")
    b = await make_element(client, "Nova Reyes")
    assert a["handle"] == "@nova-reyes" and b["handle"] == "@nova-reyes-2"
    assert a["cover"] is not None and len(a["images"]) == 1

    r = await client.patch(f"/elements/{b['id']}", json={"name": "Nova Clone"})
    assert r.json()["handle"] == "@nova-clone"
    extra = await upload(client)
    r = await client.post(f"/elements/{a['id']}/images", json=[{"asset_id": extra, "view": "side"}])
    assert len(r.json()["images"]) == 2
    r = await client.delete(f"/elements/{a['id']}/images/{extra}")
    assert len(r.json()["images"]) == 1

    listed = (await client.get("/elements", params={"q": "nova"})).json()
    assert {e["handle"] for e in listed} >= {"@nova-reyes", "@nova-clone"}
    assert (await client.delete(f"/elements/{b['id']}")).status_code == 204
    assert (await client.get(f"/elements/{b['id']}")).status_code == 404


async def estimate(client, **body) -> dict:
    r = await client.post("/registry/estimate", json={"route_policy": "cheapest_healthy", **body})
    assert r.status_code == 200, r.text
    return r.json()


async def test_mention_attaches_references_on_reference_models(client):
    juno = await make_element(client, "Juno", description="a fox with a silver collar", images=2)
    out = await client.post("/generations", json={
        "model_id": "veo-3.1", "mode": "reference", "prompt": "@juno runs through tall grass, then @juno stops",
        "params": {"duration": 8}})
    assert out.status_code == 201, out.text
    gen = out.json()
    attached = [m["asset_id"] for m in gen["request"]["media"] if m["role"] == "image_references"]
    assert attached == [i["asset"]["id"] for i in juno["images"]]
    compiled = gen["request"]["compiled_prompt"]
    assert compiled.startswith("Juno, a fox with a silver collar, runs through tall grass, then Juno stops")
    assert gen["request"]["mentions"][0]["slug"] == "juno"


async def test_seedance_uses_its_reference_tokens(client):
    await make_element(client, "Kite", description="a red kite", images=1)
    e = await estimate(client, model_id="seedance-2.5", mode="omni_reference", prompt="@kite over the bay",
                       params={"duration": 4})
    assert e["compiled_prompt"].startswith("Kite (@Image1), a red kite, over the bay")


async def test_modes_without_reference_slots_describe_in_words(client):
    await make_element(client, "Ash", description="a tall man in a grey coat")
    e = await estimate(client, model_id="veo-3.1", mode="t2v", prompt="@ash waits on the platform", params={"duration": 8})
    assert e["compiled_prompt"].startswith("Ash, a tall man in a grey coat, waits")
    assert any("can't take reference images" in w for w in e["warnings"])
    k = await estimate(client, model_id="kling-3.0", mode="t2v", prompt="@ash waits")
    assert any("make a start frame" in w for w in k["warnings"])


async def test_unknown_mentions_are_left_alone(client):
    e = await estimate(client, model_id="veo-3.1", mode="t2v", prompt="@nobody walks in", params={"duration": 8})
    assert "@nobody" in e["compiled_prompt"]
    assert any("@nobody" in w for w in e["warnings"])


async def test_references_respect_the_slot_limit(client):
    many = await make_element(client, "Crowd", images=2)
    other = await make_element(client, "Plaza", kind="location", images=1)
    own = [await upload(client), await upload(client)]
    e = await estimate(client, model_id="veo-3.1", mode="reference", prompt="@crowd fills @plaza",
                       params={"duration": 8}, media=[{"role": "image_references", "asset_id": a} for a in own])
    # 3 slots: 2 of the user's own + 1 left for Crowd; Plaza gets none
    assert any("no reference slots left for @plaza" in w for w in e["warnings"])
    assert many["handle"] == "@crowd" and other["kind"] == "location"


async def test_compile_preview_resolves_mentions_like_a_real_run(client):
    await make_element(client, "Wren", description="a girl in a green cape")
    r = await client.post("/director/compile", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "@wren waves"})
    assert r.status_code == 200, r.text
    assert r.json()["prompt"].startswith("Wren, a girl in a green cape, waves")
    assert any("can't take reference images" in w for w in r.json()["warnings"])
    # without a mode it previews the direction only
    r = await client.post("/director/compile", json={"model_id": "veo-3.1", "prompt": "@wren waves"})
    assert r.json()["prompt"].startswith("@wren waves")
    bad = await client.post("/director/compile", json={"model_id": "veo-3.1", "mode": "nope", "prompt": "x"})
    assert bad.status_code == 422


async def test_a_character_keeps_a_voice(client):
    r = await client.post("/elements", json={"name": "Nova Reyes", "voice": {"params": {"voice_id": "XrExE9yKIg1WjnnlVkGX"}}})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["voice"] == {"model_id": "eleven-v4", "params": {"voice_id": "XrExE9yKIg1WjnnlVkGX"}}
    bad = await client.patch(f"/elements/{e['id']}", json={"voice": {"params": {"voice_id": "nobody"}}})
    assert bad.status_code == 422 and "voice_id" in bad.text
    not_tts = await client.patch(f"/elements/{e['id']}", json={"voice": {"model_id": "veo-3.1", "params": {}}})
    assert not_tts.status_code == 422 and "can't speak" in not_tts.text
    cleared = await client.patch(f"/elements/{e['id']}", json={"clear_voice": True})
    assert cleared.json()["voice"] is None
