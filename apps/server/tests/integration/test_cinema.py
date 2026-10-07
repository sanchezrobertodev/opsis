"""Cinema Studio end to end on mocks: a versioned project, Claude's shot plan, hero frames, chained takes that
redo only what an edit touches, and the cut with fades."""

from __future__ import annotations

import uuid

import httpx
import pytest
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Generation
from studio.db.session import session_scope
from studio.media.ffmpeg import probe
from studio.storage.local import get_storage


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def new_project(client, **document) -> dict:
    r = await client.post("/projects", json={"title": "Harbour", "document": document})
    assert r.status_code == 201, r.text
    return r.json()


async def test_saves_are_versioned_and_revisions_restore(client):
    p = await new_project(client, logline="A keeper's last night.")
    assert p["version"] == 1 and p["document"]["settings"]["model_id"] == "seedance-2.5"
    doc = {**p["document"], "scenes": [{"heading": "EXT. LIGHTHOUSE - DUSK", "shots": [{"action": "Waves hit rocks."}]}]}
    r = await client.put(f"/projects/{p['id']}", json={"version": 1, "document": doc, "note": "first scene"})
    assert r.status_code == 200 and r.json()["version"] == 2
    shot_id = r.json()["document"]["scenes"][0]["shots"][0]["id"]

    stale = await client.put(f"/projects/{p['id']}", json={"version": 1, "document": p["document"]})
    assert stale.status_code == 409 and "version 2" in stale.text
    bad = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": {"scenes": [{"shots": [
        {"id": shot_id}, {"id": shot_id}]}]}})
    assert bad.status_code == 422 and "unique" in bad.text
    foreign = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": {"scenes": [{"shots": [
        {"take_asset_id": str(uuid.uuid4())}]}]}})
    assert foreign.status_code == 422 and "assets not found" in foreign.text

    revs = (await client.get(f"/projects/{p['id']}/revisions")).json()
    assert [(r["version"], r["note"], r["shots"]) for r in revs] == [(2, "first scene", 1), (1, "Created", 0)]
    restored = (await client.post(f"/projects/{p['id']}/revisions/1/restore")).json()
    assert restored["version"] == 3 and restored["document"]["scenes"] == []
    listed = (await client.get("/projects?kind=cinema")).json()
    assert listed[0]["id"] == p["id"] and listed[0]["shots"] == 0
    assert (await client.delete(f"/projects/{p['id']}")).status_code == 204
    assert (await client.get(f"/projects/{p['id']}")).status_code == 404


async def test_plan_proposes_scenes_without_saving(client):
    p = await new_project(client, script="A boat leaves the harbour. Gulls follow it.\n\nNight falls. The lamp turns on.")
    r = await client.post(f"/projects/{p['id']}/plan", json={"max_shots": 3})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["mock"] and [len(s["shots"]) for s in plan["scenes"]] == [2, 1]
    assert plan["warnings"] == []
    first = plan["scenes"][0]["shots"]
    assert first[0]["direction"]["shot_size"] == "wide" and first[1]["continuity"] == "continue"
    assert (await client.get(f"/projects/{p['id']}")).json()["version"] == 1  # nothing saved
    empty = await new_project(client)
    assert (await client.post(f"/projects/{empty['id']}/plan", json={})).status_code == 422


async def test_frames_takes_chaining_and_the_cut(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]

    frames = await start(client, "cinema_frames", project_id=pid, count=4, aspect_ratio="16:9", shots=[
        {"key": "sabc", "prompt": "A lighthouse at dusk"}, {"key": "sdef", "prompt": "The lamp turns on"}])
    await drain(in_memory_queue)
    done = await get(client, frames["id"])
    assert done["status"] == "succeeded", done["error"]
    hero = done["output"]["frames"]
    assert {k: len(v) for k, v in hero.items()} == {"sabc": 4, "sdef": 4}

    takes_in = {"project_id": pid, "model_id": "kling-3.0", "takes": 2, "shots": [
        {"key": "sabc", "prompt": "A lighthouse at dusk, waves", "start_asset_id": hero["sabc"][2], "duration": 5},
        {"key": "sdef", "prompt": "The lamp turns on", "continue_from": "sabc", "duration": 5}]}
    run = await start(client, "cinema_takes", **takes_in)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"take[sabc:1]", "take[sabc:2]", "chain[sdef]", "take[sdef:1]", "take[sdef:2]"}
    async with session_scope() as s:
        first = await s.get(Generation, uuid.UUID(steps["take[sabc:1]"]["generation_id"]))
        chained = await s.get(Generation, uuid.UUID(steps["take[sdef:1]"]["generation_id"]))
    assert (str(first.project_id), first.shot_key) == (pid, "sabc")
    assert [m["asset_id"] for m in first.request["media"]] == [hero["sabc"][2]]
    assert [m["asset_id"] for m in chained.request["media"]] == [steps["chain[sdef]"]["output"]["asset_id"]]

    listed = (await client.get(f"/projects/{pid}/takes")).json()
    kinds = {(t["shot_key"], t["output_type"]) for t in listed if t["asset"]}
    assert kinds == {("sabc", "image"), ("sdef", "image"), ("sabc", "video"), ("sdef", "video")}
    assert len([t for t in listed if t["shot_key"] == "sdef" and t["output_type"] == "video"]) == 2

    # rewriting the second shot redoes only it; the first shot's takes are kept
    takes_in["shots"][1]["prompt"] = "The lamp flickers, then holds"
    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": takes_in})
    assert r.status_code == 200
    await drain(in_memory_queue)
    again = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}
    assert again["take[sabc:1]"]["generation_id"] == steps["take[sabc:1]"]["generation_id"]
    assert again["take[sdef:1]"]["generation_id"] != steps["take[sdef:1]"]["generation_id"]

    a, b = again["take[sabc:1]"]["output"], again["take[sdef:2]"]["output"]
    cut = await start(client, "cinema_export", project_id=pid, title="Harbour", fade_seconds=0.5, clips=[
        {"asset_id": a["asset_id"], "trim_in": 1.0},
        {"asset_id": b["asset_id"], "trim_out": 3.0, "transition": "fade"}])
    await drain(in_memory_queue)
    film = await get(client, cut["id"])
    assert film["status"] == "succeeded", film["error"]
    async with session_scope() as s:
        from studio.db.models import Asset

        asset = await s.get(Asset, uuid.UUID(film["output"]["asset_id"]))
    info = await probe(get_storage().path_for(asset.storage_key))
    assert info.has_audio and info.duration_ms / 1000 == pytest.approx(4.0 + 3.0 - 0.5, abs=0.3)


async def test_takes_need_an_owned_project(client, in_memory_queue):
    r = await client.post("/generations", json={"model_id": "kling-3.0", "mode": "t2v", "prompt": "x",
                                                "project_id": str(uuid.uuid4()), "shot_key": "sabc"})
    assert r.status_code == 422 and "project" in r.text
    bad = await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        "project_id": str(uuid.uuid4()), "model_id": "kling-3.0", "shots": [
            {"key": "sabc", "prompt": "x", "continue_from": "sdef"}, {"key": "sdef", "prompt": "y"}]}})
    assert bad.status_code == 422 and "must come before it" in bad.text


async def test_frames_for_a_mentioned_character_use_its_pictures(client, in_memory_queue):
    from test_elements import make_element

    maya = await make_element(client, "Maya", description="a lighthouse keeper in a yellow coat")
    p = await new_project(client, cast=[maya["slug"]])
    shots = [{"key": "sabc", "prompt": f"@{maya['slug']} climbs the stairs"}]
    est = await client.post("/pipelines/estimate", json={"recipe": "cinema_frames", "input": {
        "project_id": p["id"], "shots": shots}})
    assert est.status_code == 200, est.text
    run = await start(client, "cinema_frames", project_id=p["id"], count=2, shots=shots)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(done["steps"][0]["generation_id"]))
    assert gen.mode == "edit" and [m["role"] for m in gen.request["media"]] == ["image_references"]
