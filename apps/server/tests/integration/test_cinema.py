"""Cinema Studio end to end on mocks: a versioned project, Claude's shot plan, hero frames, chained takes that
redo only what an edit touches, and the cut with fades."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Generation, GenerationAttempt, SpendLedger
from studio.db.session import session_scope
from studio.domain.enums import AttemptStatus, LedgerEntry
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
    assert p["version"] == 1 and p["document"]["settings"]["model_id"] == "cinema-studio"
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


async def test_director_chat_proposes_edits_as_a_diff_without_saving(client):
    p = await new_project(client, scenes=[{"id": "cdock", "heading": "EXT. DOCK", "shots": [
        {"id": "sone", "action": "Fog.", "direction": {"shot_size": "wide", "palette": "teal_orange"}},
        {"id": "stwo", "action": "A rope.", "continuity": "continue"}]}])
    r = await client.post(f"/projects/{p['id']}/director", json={"message": "Make shot 2 tighter"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mock"] and "Shot 2" in out["reply"] and out["warnings"] == []
    assert out["changes"] == [{"kind": "changed", "scene_id": "cdock", "shot_id": "stwo", "label": "Shot 2", "fields": [
        {"field": "shot_size", "before": None, "after": "close_up"},
        {"field": "camera_moves", "before": None, "after": "dolly_in"}]}]
    assert out["scenes"][0]["shots"][1]["continuity"] == "continue"
    assert (await client.get(f"/projects/{p['id']}")).json()["version"] == 1  # nothing saved

    # the storyboard on screen wins over the saved one, and the history rides along
    on_screen = [{"id": "cdock", "heading": "EXT. DOCK", "shots": [{"id": "sone", "action": "Fog."}]}]
    r = await client.post(f"/projects/{p['id']}/director", json={
        "message": "remove shot 1", "scenes": on_screen,
        "history": [{"role": "user", "text": "Make shot 2 tighter"}, {"role": "director", "text": "Done."}]})
    assert [c["kind"] for c in r.json()["changes"]] == ["removed"] and r.json()["scenes"][0]["shots"] == []
    bad = await client.post(f"/projects/{p['id']}/director", json={"message": "hi", "scenes": [{"shots": [
        {"id": "sone"}, {"id": "sone"}]}]})
    assert bad.status_code == 422 and "isn't valid" in bad.text


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


async def test_cinema_studio_runs_on_its_engine(client, in_memory_queue):
    r = await client.post("/registry/estimate", json={
        "model_id": "cinema-studio", "mode": "t2v", "prompt": "A keeper climbs the stairs",
        "params": {"engine": "veo", "duration": 8, "era": "eighties"}})
    assert r.status_code == 200, r.text
    est = r.json()
    assert (est["model_id"], est["via"]["model_id"], est["via"]["engine"]) == ("veo-3.1", "cinema-studio", "veo")
    assert "1980s look" in est["compiled_prompt"] and est["chosen_route_id"] == "veo-3.1@mock"

    # a film shot on Cinema Studio: the takes run on the engine and remember the studio they came through
    p = await new_project(client)
    run = await start(client, "cinema_takes", project_id=p["id"], model_id="cinema-studio",
                      params={"engine": "kling", "palette": "sepia"}, takes=1,
                      shots=[{"key": "saaa", "prompt": "Waves hit the rocks", "duration": 5}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(done["steps"][0]["generation_id"]))
    assert (gen.model_id, gen.mode, gen.shot_key) == ("kling-3.0", "t2v", "saaa")
    assert gen.request["via"]["model_id"] == "cinema-studio" and gen.request["direction"] == {"palette": "sepia"}
    assert "sepia monochrome" in gen.compiled["prompt"]

    bad = await client.post("/registry/estimate", json={"model_id": "cinema-studio", "mode": "omni_reference",
                                                        "params": {"engine": "kling"}})
    assert bad.status_code == 422 and "Seedance 2.5 or Veo 3.1 can" in bad.text


async def made_by(client, pid: str, shot: str) -> dict[str, dict]:
    """The shot's finished video takes, by how they were made (the newest of each)."""
    out: dict[str, dict] = {}
    for t in (await client.get(f"/projects/{pid}/takes", params={"shot_key": shot})).json():
        if t["asset"] and t["output_type"] == "video":
            out.setdefault(t["made_by"], t)
    return out


async def test_a_scene_in_one_multi_shot_take_becomes_a_take_per_shot(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    frames = await start(client, "cinema_frames", project_id=pid, count=1, shots=[{"key": "sone", "prompt": "Cliff"}])
    await drain(in_memory_queue)
    hero = (await get(client, frames["id"]))["output"]["frames"]["sone"][0]

    shots = [{"key": "sone", "prompt": "Wide on the lighthouse as the storm hits", "duration": 4,
              "direction": {"shot_size": "wide", "palette": "cold_steel"}},
             {"key": "stwo", "prompt": "Close on the keeper's hand on the brass key", "duration": 3,
              "direction": {"shot_size": "close_up"}},
             {"key": "sthree", "prompt": "The lamp flares and the beam sweeps the sea", "duration": 5}]
    run = await start(client, "cinema_scene", project_id=pid, model_id="kling-3.0", scene_key="cone",
                      start_asset_id=hero, shots=shots)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"scene[cone:1]", "split[cone:1]"}
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(steps["scene[cone:1]"]["generation_id"]))
    # one call: the shots go as the model's own shot list, each compiled with its direction; 12 s in all
    assert (gen.mode, gen.shot_key, gen.request["params"]["duration"]) == ("multi_shot_frame", None, 12)
    sent = gen.request["params"]["shots"]
    assert [s["duration"] for s in sent] == [4, 3, 5] and "cold steel-blue" in sent[0]["prompt"]
    assert sent[1]["prompt"].startswith("Close on the keeper's hand on the brass key, close-up")
    assert gen.request["compiled_prompt"] == "" and gen.compiled["prompt"].startswith("Shot 1: Wide on")

    scene_take = done["output"]["scene_takes"][0]
    lengths = {}
    for key in ("sone", "stwo", "sthree"):
        take = (await made_by(client, pid, key))["multi_shot"]
        assert take["parent_asset_id"] == scene_take and take["generation_id"] is None
        assert done["output"]["takes"][key] == [take["asset"]["id"]]
        lengths[key] = round(take["asset"]["duration_ms"] / 1000)
    assert lengths == {"sone": 4, "stwo": 3, "sthree": 5}

    too_long = await client.post("/pipelines", json={"recipe": "cinema_scene", "input": {
        "project_id": pid, "model_id": "kling-3.0", "scene_key": "cone",
        "shots": [{**s, "duration": 6} for s in shots]}})
    assert too_long.status_code == 422 and "at most 15 s in one take" in too_long.text
    no_multi = await client.post("/pipelines", json={"recipe": "cinema_scene", "input": {
        "project_id": pid, "model_id": "veo-3.1", "scene_key": "cone", "shots": shots}})
    assert no_multi.status_code == 422 and "can't film several shots in one take" in no_multi.text


async def test_extend_a_take_forward_and_backward_and_edit_it(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    run = await start(client, "cinema_takes", project_id=pid, model_id="kling-3.0", takes=1,
                      shots=[{"key": "sone", "prompt": "The keeper climbs the stairs", "duration": 5}])
    await drain(in_memory_queue)
    take = (await get(client, run["id"]))["output"]["takes"]["sone"][0]

    after = await start(client, "cinema_extend", project_id=pid, shot_key="sone", take_asset_id=take, side="after",
                        prompt="He reaches the lamp room", model_id="kling-3.0", duration=4)
    before = await start(client, "cinema_extend", project_id=pid, shot_key="sone", take_asset_id=take,
                         side="before", prompt="He unlocks the door at the foot of the stairs",
                         model_id="cinema-studio", params={"engine": "kling"}, duration=3)
    edit = await start(client, "cinema_edit", project_id=pid, shot_key="sone", take_asset_id=take,
                       model_id="kling-o3-edit", prompt="Make it snow outside the windows")
    await drain(in_memory_queue)
    for r in (after, before, edit):
        done = await get(client, r["id"])
        assert done["status"] == "succeeded", (r["recipe"], done["error"])
    steps = {s["key"] for s in (await get(client, before["id"]))["steps"]}
    assert steps == {"edge", "lead_frame", "lead", "join"}

    takes = await made_by(client, pid, "sone")
    assert set(takes) == {"model", "extend_after", "extend_before", "edit"}
    assert all(takes[k]["parent_asset_id"] == take for k in ("extend_after", "extend_before", "edit"))
    # the joins drop the one frame the two parts share
    assert abs(takes["extend_after"]["asset"]["duration_ms"] - 9000) < 200
    assert abs(takes["extend_before"]["asset"]["duration_ms"] - 8000) < 200
    assert takes["edit"]["generation_id"] is not None

    veo = await client.post("/pipelines", json={"recipe": "cinema_extend", "input": {
        "project_id": pid, "shot_key": "sone", "take_asset_id": take, "side": "before", "prompt": "x",
        "model_id": "wan-2.2-5b"}})
    assert veo.status_code == 422 and "can't end on a chosen frame" in veo.text


async def film_two_takes(client, queue, pid: str) -> tuple[str, str]:
    """Two takes of shot sone, oldest first."""
    takes = []
    for _ in range(2):
        run = await start(client, "cinema_takes", project_id=pid, model_id="kling-3.0", takes=1,
                          shots=[{"key": "sone", "prompt": "Waves hit the rocks", "duration": 5}])
        await drain(queue)
        takes.append((await get(client, run["id"]))["output"]["takes"]["sone"][0])
    return takes[0], takes[1]


async def test_picked_takes_are_listed_however_old(client, in_memory_queue, monkeypatch):
    p = await new_project(client)
    pid = p["id"]
    old, new = await film_two_takes(client, in_memory_queue, pid)
    doc = {**p["document"], "scenes": [{"id": "cone", "shots": [{"id": "sone", "action": "Waves",
                                                                   "take_asset_id": old}]}]}
    assert (await client.put(f"/projects/{pid}", json={"version": 1, "document": doc})).status_code == 200

    monkeypatch.setattr("studio.api.routers.projects.TAKES_PAGE", 1)  # only the newest fits on the page
    for query in ({}, {"shot_key": "sone"}):
        listed = (await client.get(f"/projects/{pid}/takes", params=query)).json()
        assert [t["asset"]["id"] for t in listed] == [new, old]


async def test_restoring_a_version_unpicks_what_was_deleted_since(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    old, new = await film_two_takes(client, in_memory_queue, pid)
    doc = {**p["document"], "scenes": [{"id": "cone", "shots": [
        {"id": "sone", "action": "Waves", "take_asset_id": old},
        {"id": "stwo", "action": "Gulls", "take_asset_id": new}]}]}
    assert (await client.put(f"/projects/{pid}", json={"version": 1, "document": doc})).status_code == 200
    r = await client.delete(f"/assets/{old}")
    assert r.status_code == 409 and "the film" in r.json()["detail"].lower()  # the film still plays it
    doc["scenes"][0]["shots"][0]["take_asset_id"] = None
    assert (await client.put(f"/projects/{pid}", json={"version": 2, "document": doc})).status_code == 200
    assert (await client.delete(f"/assets/{old}")).status_code == 204

    r = await client.post(f"/projects/{pid}/revisions/2/restore")
    assert r.status_code == 200, r.text
    shots = r.json()["document"]["scenes"][0]["shots"]
    assert [s["take_asset_id"] for s in shots] == [None, new]
    revisions = (await client.get(f"/projects/{pid}/revisions")).json()
    assert revisions[0]["note"] == "Restored version 2; 1 pick deleted since was cleared"
    # and the restored document saves like any other
    assert (await client.put(f"/projects/{pid}", json={"version": 4, "document": r.json()["document"]})).status_code == 200


DIALOGUE = [{"who": "Nova", "text": "It goes dark tonight.", "voice_id": "XrExE9yKIg1WjnnlVkGX"},
            {"who": "Kai", "text": "Then we light it by hand.", "voice_id": "nPczCjzI2devNBz1zQrb"}]


async def test_dialogue_is_spoken_by_models_that_voice_it(client, in_memory_queue):
    p = await new_project(client)
    run = await start(client, "cinema_takes", project_id=p["id"], model_id="kling-3.0", takes=1, shots=[
        {"key": "sone", "prompt": "Nova turns from the window", "duration": 5, "direction": {"dialogue": DIALOGUE}}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == ["take[sone:1]"]  # nothing voiced separately
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(done["steps"][0]["generation_id"]))
    assert 'Nova says: "It goes dark tonight."' in gen.compiled["prompt"]
    assert 'Kai says: "Then we light it by hand."' in gen.compiled["prompt"]
    assert gen.request["params"]["generate_audio"] is True  # Kling speaks only with its sound on


async def test_dialogue_is_voiced_and_lip_synced_on_models_that_dont(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    est = (await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        "project_id": pid, "model_id": "runway-gen-4.5", "takes": 1,
        "shots": [{"key": "sone", "prompt": "Nova turns from the window", "duration": 5,
                   "direction": {"dialogue": DIALOGUE}}]}})).json()
    assert est["generations"] == 4  # the take, two lines, one lip sync

    run = await start(client, "cinema_takes", project_id=pid, model_id="runway-gen-4.5", takes=1, shots=[
        {"key": "sone", "prompt": "Nova turns from the window", "duration": 5, "direction": {"dialogue": DIALOGUE}}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"take[sone:1]", "line[sone:1]", "line[sone:2]", "track[sone]", "lip[sone:1]"}
    async with session_scope() as s:
        take = await s.get(Generation, uuid.UUID(steps["take[sone:1]"]["generation_id"]))
        line = await s.get(Generation, uuid.UUID(steps["line[sone:2]"]["generation_id"]))
    assert "says:" not in take.compiled["prompt"] and line.request["params"]["voice_id"] == "nPczCjzI2devNBz1zQrb"

    raw = steps["take[sone:1]"]["output"]["asset_id"]
    synced = steps["lip[sone:1]"]["output"]["asset_id"]
    assert done["output"] == {"takes": {"sone": [raw]}, "lip_synced": {"sone": [synced]}}
    takes = {t["made_by"]: t for t in (await client.get(f"/projects/{pid}/takes")).json()}
    assert takes["lipsync"]["asset"]["id"] == synced and takes["lipsync"]["parent_asset_id"] == raw
    # the lip-synced take carries the whole dialogue: both lines and the pause between them
    track_ms = steps["track[sone]"]["output"]["duration_ms"]
    lines = [(await client.get(f"/assets/{steps[k]['output']['asset_id']}")).json() for k in ("line[sone:1]", "line[sone:2]")]
    assert abs(track_ms - sum(x["duration_ms"] for x in lines) - 350) < 150
    assert abs(takes["lipsync"]["asset"]["duration_ms"] - track_ms) < 200

    # for the cut's dialogue track: the speech the picked take was lip-synced to
    doc = {"scenes": [{"id": "cone", "shots": [{"id": "sone", "action": "x", "take_asset_id": synced}]}]}
    assert (await client.put(f"/projects/{pid}", json={"version": 1, "document": doc})).status_code == 200
    (dialogue,) = (await client.get(f"/projects/{pid}/dialogue")).json()
    track = steps["track[sone]"]["output"]["asset_id"]
    assert dialogue["shot_key"] == "sone" and dialogue["asset"]["id"] == track and dialogue["lip_synced"] is True
    assert abs(dialogue["asset"]["duration_ms"] - track_ms) < 50  # joined audio knows its length
    # a raw take still finds the shot's track among the run's outputs
    doc["scenes"][0]["shots"][0]["take_asset_id"] = raw
    assert (await client.put(f"/projects/{pid}", json={"version": 2, "document": doc})).status_code == 200
    (dialogue,) = (await client.get(f"/projects/{pid}/dialogue")).json()
    assert dialogue["asset"]["id"] == track and dialogue["lip_synced"] is False

    # a take extended from the lip-synced one still carries its speech from the start: muting it stays right
    run = await start(client, "cinema_extend", project_id=pid, shot_key="sone", take_asset_id=synced, side="after",
                      prompt="She looks back at the sea", model_id="runway-gen-4.5", duration=5)
    await drain(in_memory_queue)
    longer = (await get(client, run["id"]))["output"]["asset_id"]
    doc["scenes"][0]["shots"][0]["take_asset_id"] = longer
    assert (await client.put(f"/projects/{pid}", json={"version": 3, "document": doc})).status_code == 200
    (dialogue,) = (await client.get(f"/projects/{pid}/dialogue")).json()
    assert dialogue["asset"]["id"] == track and dialogue["lip_synced"] is True
    # a part joined before it moves the speech later: the shot keeps it in its own sound and offers no track
    run = await start(client, "cinema_extend", project_id=pid, shot_key="sone", take_asset_id=synced, side="before",
                      prompt="She climbs the last stairs", model_id="kling-3.0", duration=5)
    await drain(in_memory_queue)
    led = await get(client, run["id"])
    assert led["status"] == "succeeded", led["error"]
    doc["scenes"][0]["shots"][0]["take_asset_id"] = led["output"]["asset_id"]
    assert (await client.put(f"/projects/{pid}", json={"version": 4, "document": doc})).status_code == 200
    assert (await client.get(f"/projects/{pid}/dialogue")).json() == []


async def test_the_estimate_and_the_spend_split_by_shot(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    body = {"project_id": pid, "model_id": "runway-gen-4.5", "takes": 2, "shots": [
        {"key": "sone", "prompt": "Nova turns from the window", "duration": 5, "direction": {"dialogue": DIALOGUE}},
        {"key": "stwo", "prompt": "The lamp turns on", "duration": 10, "continue_from": "sone"}]}
    est = (await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": body})).json()
    assert set(est["by_key"]) == {"sone", "stwo"} and sum(est["by_key"].values()) == pytest.approx(est["estimate_usd"])
    live = est["live_by_key"]
    assert set(live) == {"sone", "stwo"} and sum(live.values()) == pytest.approx(est["live_estimate_usd"])
    # mock routes are free; on the paid ones a 10 s shot costs more than a 5 s one without its lines and lip syncs
    assert est["estimate_usd"] == 0 and live["stwo"] > 0 and live["sone"] > 0
    lone = (await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        **body, "shots": [{**body["shots"][0], "direction": {}}]}})).json()
    assert live["sone"] > lone["live_by_key"]["sone"]  # its lines are voiced and the takes lip-synced

    run = await start(client, "cinema_takes", **{**body, "takes": 1})
    await drain(in_memory_queue)
    steps = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}
    # charges as the ledger keeps them: a failed attempt the provider still billed counts, not just the cost the
    # generation ended up with
    async with session_scope() as s:
        for n, (key, usd) in enumerate((("take[sone:1]", "0.50"), ("lip[sone:1]", "0.25"), ("take[stwo:1]", "0.40"),
                                        ("take[stwo:1]", "0.60"))):
            g = await s.get(Generation, uuid.UUID(steps[key]["generation_id"]))
            attempt = GenerationAttempt(generation_id=g.id, attempt_no=100 + n, route_id="test-route",
                                        provider="test", status=AttemptStatus.failed)
            s.add(attempt)
            await s.flush()
            s.add(SpendLedger(user_id=g.user_id, entry_type=LedgerEntry.charge, amount_usd=Decimal(usd),
                              generation_attempt_id=attempt.id))
            # a settled reservation isn't spend (and an open one would hold the test user's daily cap)
            s.add(SpendLedger(user_id=g.user_id, entry_type=LedgerEntry.reservation, amount_usd=Decimal("9"),
                              generation_attempt_id=attempt.id))
            s.add(SpendLedger(user_id=g.user_id, entry_type=LedgerEntry.release, amount_usd=Decimal("-9"),
                              generation_attempt_id=attempt.id))
        await s.commit()
    spend = (await client.get(f"/projects/{pid}/spend")).json()
    assert spend["shots"]["sone"] == pytest.approx(0.75) and spend["shots"]["stwo"] == pytest.approx(1.0)
    assert spend["total_usd"] == pytest.approx(sum(spend["shots"].values()))
    other = await new_project(client)
    assert (await client.get(f"/projects/{other['id']}/spend")).json() == {"total_usd": 0, "shots": {}}


async def test_old_free_text_dialogue_becomes_lines(client):
    p = await new_project(client)
    doc = {**p["document"], "scenes": [{"id": "cone", "shots": [
        {"id": "sone", "action": "x", "dialogue": "@nova: It goes dark.\nKai: Then we light it."}]}]}
    r = await client.put(f"/projects/{p['id']}", json={"version": 1, "document": doc})
    assert r.status_code == 200, r.text
    assert r.json()["document"]["scenes"][0]["shots"][0]["lines"] == [
        {"who": "@nova", "text": "It goes dark.", "voice_id": None},
        {"who": "Kai", "text": "Then we light it.", "voice_id": None}]


async def test_a_scene_with_dialogue_turns_the_sound_on(client, in_memory_queue):
    p = await new_project(client)
    run = await start(client, "cinema_scene", project_id=p["id"], model_id="kling-3.0", scene_key="cone", shots=[
        {"key": "sone", "prompt": "Nova at the window", "duration": 4, "direction": {"dialogue": DIALOGUE[:1]}},
        {"key": "stwo", "prompt": "Kai in the doorway", "duration": 4,
         "direction": {"dialogue": [*DIALOGUE[1:], {"who": "Kai", "text": " "}]}}])  # a blank line is skipped
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(done["steps"][0]["generation_id"]))
    assert gen.request["params"]["generate_audio"] is True  # Kling is silent unless asked
    assert 'Kai says: "Then we light it by hand."' in gen.request["params"]["shots"][1]["prompt"]


async def test_a_malformed_direction_is_a_request_error(client):
    p = await new_project(client)
    for direction, why in (({"dialogue": ["hello"]}, "list of lines"), ({"framing": "wide"}, "Extra inputs"),
                           ({"dialogue": [{"who": "Kai", "text": "x" * 600}]}, "at most 500")):
        r = await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
            "project_id": p["id"], "model_id": "kling-3.0",
            "shots": [{"key": "sone", "prompt": "x", "direction": direction}]}})
        assert r.status_code == 422 and why in r.text, r.text


async def test_a_three_by_three_grid_and_closing_frames(client, in_memory_queue):
    p = await new_project(client)
    pid = p["id"]
    # nine frames on a model that makes four at a time: three calls (4 + 4 + 1)
    run = await start(client, "cinema_frames", project_id=pid, count=9, shots=[{"key": "sabc", "prompt": "A pier at dawn"}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert sorted(s["key"] for s in done["steps"]) == ["frames[sabc:1]", "frames[sabc:2]", "frames[sabc:3]"]
    assert len(done["output"]["frames"]["sabc"]) == 9
    async with session_scope() as s:
        sizes = sorted([(await s.get(Generation, uuid.UUID(x["generation_id"]))).request["params"].get("num_images", 1)
                        for x in done["steps"]])
    assert sizes == [1, 4, 4]

    ends = await start(client, "cinema_frames", project_id=pid, count=4, which="end",
                       shots=[{"key": "sabc", "prompt": "A pier at dawn"}])
    await drain(in_memory_queue)
    ended = await get(client, ends["id"])
    assert [s["key"] for s in ended["steps"]] == ["ends[sabc]"] and len(ended["output"]["frames"]["sabc"]) == 4
    frames = [t for t in (await client.get(f"/projects/{pid}/takes")).json() if t["output_type"] == "image" and t["asset"]]
    assert sorted({t["frame"] for t in frames}) == ["end", "start"]
    assert sum(t["frame"] == "end" for t in frames) == 4

    # a take from the picked opening frame to the picked closing one
    opening = done["output"]["frames"]["sabc"][0]
    closing = ended["output"]["frames"]["sabc"][0]
    doc = {"scenes": [{"id": "cone", "shots": [{"id": "sabc", "action": "A pier at dawn", "hero_asset_id": opening,
                                                "end_asset_id": closing}]}]}
    assert (await client.put(f"/projects/{pid}", json={"version": 1, "document": doc})).status_code == 200
    takes = await start(client, "cinema_takes", project_id=pid, model_id="kling-3.0", takes=1, shots=[
        {"key": "sabc", "prompt": "A pier at dawn", "start_asset_id": opening, "end_asset_id": closing, "duration": 5}])
    await drain(in_memory_queue)
    filmed = await get(client, takes["id"])
    assert filmed["status"] == "succeeded", filmed["error"]
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(filmed["steps"][0]["generation_id"]))
    assert {m["role"]: m["asset_id"] for m in gen.request["media"]} == {"start_image": opening, "end_image": closing}


async def test_a_closing_frame_needs_an_opening_one_and_a_model_that_takes_both(client):
    p = await new_project(client)
    shot = {"key": "sabc", "prompt": "x", "end_asset_id": str(uuid.uuid4())}
    r = await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        "project_id": p["id"], "model_id": "kling-3.0", "shots": [shot]}})
    assert r.status_code == 422 and "needs an opening frame too" in r.text
    r = await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        "project_id": p["id"], "model_id": "wan-2.2-14b", "shots": [{**shot, "start_asset_id": str(uuid.uuid4())}]}})
    assert r.status_code == 422 and "can't end on a picked frame" in r.text


@pytest.mark.parametrize(("engine", "base"), [("seedance", "seedance-2.5/i2v"), ("kling", "kling-3.0/i2v"),
                                               ("veo", "veo-3.1/flf2v")])
async def test_a_closing_frame_runs_on_each_engines_own_mode(client, engine, base):
    p = await new_project(client)
    r = await client.post("/pipelines/estimate", json={"recipe": "cinema_takes", "input": {
        "project_id": p["id"], "model_id": "cinema-studio", "takes": 1, "params": {"engine": engine},
        "shots": [{"key": "sabc", "prompt": "A pier at dawn", "duration": 8, "start_asset_id": str(uuid.uuid4()),
                   "end_asset_id": str(uuid.uuid4())}]}})
    assert r.status_code == 200, r.text
    from studio.pipelines.recipes.cinema import take_mode
    from studio.registry.loader import get_registry
    from studio.registry.virtual import resolve

    reg = get_registry()
    spec = reg.model("cinema-studio")
    from studio.pipelines.recipes.cinema import TakeShot
    mode = take_mode(spec, TakeShot(key="sabc", prompt="x", start_asset_id=uuid.uuid4(), end_asset_id=uuid.uuid4()))
    v = resolve(reg, spec, mode.id, {"engine": engine}, None)
    assert f"{v.spec.id}/{v.mode}" == base



async def test_a_plans_recurring_people_become_a_cast_with_pictures_that_frames_use(client, in_memory_queue):
    p = await new_project(client, script="A woman walks to the pier. She looks out to sea.\n\nShe turns home.")
    plan = (await client.post(f"/projects/{p['id']}/plan", json={"max_shots": 4})).json()
    (lead,) = plan["characters"]
    assert lead["handle"] == "lead" and lead["description"]
    assert all(s["shots"][0]["action"].startswith("@lead ") for s in plan["scenes"])

    r = await client.post(f"/projects/{p['id']}/cast", json={"characters": plan["characters"], "scenes": plan["scenes"]})
    assert r.status_code == 201, r.text
    cast = r.json()
    (member,) = cast["cast"]
    slug = member["slug"]  # "lead", or "lead-2" when an earlier test already saved one
    assert all(s["shots"][0]["action"].startswith(f"@{slug} ") for s in cast["scenes"])
    element = (await client.get(f"/elements/{member['id']}")).json()
    assert element["kind"] == "character" and element["description"] == lead["description"] and element["images"] == []

    run = await start(client, "cinema_cast", project_id=p["id"], characters=[{"element_id": member["id"], "slug": slug}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    picture = done["output"]["pictures"][slug]
    element = (await client.get(f"/elements/{member['id']}")).json()
    assert [i["asset"]["id"] for i in element["images"]] == [picture] and element["cover"]["id"] == picture
    portrait = (await client.get(f"/generations/{done['steps'][0]['generation_id']}")).json()
    assert portrait["request"]["compiled_prompt"].startswith(f"Character reference portrait of {member['name']}, ")

    # a hero frame of a shot that mentions them now carries their picture as the reference
    shot = cast["scenes"][0]["shots"][0]
    frames = await start(client, "cinema_frames", project_id=p["id"], count=1,
                         shots=[{"key": shot["id"], "prompt": shot["action"], "direction": shot["direction"]}])
    await drain(in_memory_queue)
    frame = await get(client, frames["id"])
    gen = (await client.get(f"/generations/{frame['steps'][0]['generation_id']}")).json()
    assert gen["mode"] == "edit" and [m["asset_id"] for m in gen["request"]["media"]] == [picture]


async def test_a_handle_nobody_has_reads_as_a_word(client):
    from studio.cinema.document import Scene
    from studio.cinema.planner import PlannedCharacter, _unmention, new_characters

    scene = Scene.model_validate({"heading": "x", "shots": [
        {"action": "@keeper climbs; @ghost waits.", "lines": [{"who": "@ghost", "text": "Boo."}],
         "direction": {"cast_emotions": [{"who": "@ghost", "emotion": "fear"}]}}]})
    found = new_characters([PlannedCharacter(handle="@Keeper", name="The Keeper", look="old, grey coat"),
                            PlannedCharacter(handle="unused", name="Nobody", look="-")], [scene], set())
    assert [c.handle for c in found] == ["keeper"]
    shot = _unmention(scene, {"keeper"}).shots[0]
    assert shot.action == "@keeper climbs; ghost waits." and shot.lines[0].who == "ghost"
    assert shot.direction.cast_emotions == []


async def test_saving_a_cast_never_overfills_the_film(client):
    p = await new_project(client, script="x")
    people = [{"handle": f"p{i}", "name": f"P{i}", "description": "-"} for i in range(3)]
    r = await client.post(f"/projects/{p['id']}/cast", json={
        "characters": people, "scenes": [], "cast": [f"c{i}" for i in range(10)]})
    assert r.status_code == 422 and "keep at most 2" in r.json()["detail"]
    assert (await client.get("/elements", params={"q": "P0"})).json() == []  # nothing was created
