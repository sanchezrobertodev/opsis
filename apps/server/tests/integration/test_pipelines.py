"""Recipe runs end to end on the mock provider: the real API, engine, worker tasks and ffmpeg.

`drain()` plays the worker: it runs queued jobs through their real task functions until the queue is
empty, giving after-commit wake-ups a moment to land between rounds.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Asset, AssetLineage, Generation
from studio.db.session import session_scope
from studio.media.ffmpeg import probe
from studio.storage.local import get_storage
from studio.worker.app import app as worker


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def drain(queue, rounds: int = 300) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.02)
        todo = sorted((j for j in queue.jobs.values() if j["status"] == "todo"), key=lambda j: j["id"])
        if not todo:
            await asyncio.sleep(0.05)
            if not any(j["status"] == "todo" for j in queue.jobs.values()):
                return
            continue
        for job in todo:
            job["status"] = "doing"
            await worker.tasks[job["task_name"]].func(**job["args"])
            job["status"] = "succeeded"
    raise AssertionError("queue never drained")


async def start(client, recipe: str, **inp) -> dict:
    r = await client.post("/pipelines", json={"recipe": recipe, "input": inp})
    assert r.status_code == 201, r.text
    return r.json()


async def get(client, run_id: str) -> dict:
    return (await client.get(f"/pipelines/{run_id}")).json()


async def test_sequence_chains_last_frames_and_joins_the_cut(client, in_memory_queue):
    run = await start(client, "sequence", model_id="veo-3.1", shots=[
        {"prompt": "a lighthouse at dusk", "duration": 4},
        {"prompt": "the lamp turns on", "duration": 4},
        {"prompt": "waves crash below", "duration": 4},
    ])
    assert run["status"] == "running" and run["estimate_usd"] == 0  # mock routes are free
    await drain(in_memory_queue)

    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == [
        "shot[01]", "frame[01]", "shot[02]", "frame[02]", "shot[03]", "cut"]
    assert all(s["status"] == "succeeded" for s in done["steps"])

    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        shot2 = await s.get(Generation, uuid.UUID(steps["shot[02]"]["generation_id"]))
        assert shot2.mode == "i2v"
        assert [(m["role"], m["asset_id"]) for m in shot2.request["media"]] == [
            ("start_image", steps["frame[01]"]["output"]["asset_id"])]
        cut = await s.get(Asset, uuid.UUID(done["output"]["asset_id"]))
        parents = (await s.execute(select(AssetLineage).where(AssetLineage.child_asset_id == cut.id))).scalars().all()
    assert len(parents) == 3 and cut.source.value == "pipeline"
    info = await probe(get_storage().path_for(cut.storage_key))
    assert info.duration_ms == pytest.approx(12_000, abs=600) and info.has_audio
    assert done["output_asset"]["id"] == done["output"]["asset_id"]

    listed = (await client.get("/pipelines")).json()
    assert listed[0]["id"] == run["id"] and listed[0]["steps_done"] == listed[0]["steps_total"] == 6


async def test_compare_waits_for_a_pick_and_survives_a_failed_take(client, in_memory_queue):
    run = await start(client, "compare", prompt="a paper boat in the rain", entries=[
        {"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}, {"model_id": "veo-3.1", "params": {"duration": 4}},
    ])
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting"
    pick = next(s for s in waiting["steps"] if s["key"] == "pick")
    assert pick["status"] == "waiting" and len(pick["options"]) == 3
    assert {o["label"] for o in pick["options"]} == {"Kling 3.0", "Seedance 2.5", "Veo 3.1"}

    bad = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": str(uuid.uuid4())})
    assert bad.status_code == 409
    choice = pick["options"][1]["value"]
    r = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": choice})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded" and done["output"]["asset_id"] == choice
    assert (await client.get(f"/assets/{choice}")).json()["favorite"] is True

    # one model failing doesn't sink a comparison; with a single good take there is nothing to pick
    run = await start(client, "compare", prompt="a kite [[mock:fail=moderation]]", entries=[
        {"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}])
    await drain(in_memory_queue)
    failed_both = await get(client, run["id"])
    assert failed_both["status"] == "succeeded" and failed_both["output"]["asset_id"] is None
    assert all(t["error"] for t in failed_both["output"]["takes"])


async def test_a_failed_shot_fails_the_run_and_retry_redoes_only_it(client, in_memory_queue):
    run = await start(client, "sequence", model_id="kling-3.0", assemble=False, shots=[
        {"prompt": "a fox in the snow"}, {"prompt": "the fox leaps [[mock:fail=moderation]]"}])
    await drain(in_memory_queue)
    failed = await get(client, run["id"])
    assert failed["status"] == "failed" and failed["error"].startswith("Shot 2:")
    first_gen = failed["steps"][0]["generation_id"]

    r = await client.post(f"/pipelines/{run['id']}/retry")
    assert r.status_code == 200
    await drain(in_memory_queue)
    again = await get(client, run["id"])
    assert again["status"] == "failed"  # the prompt still trips moderation
    assert again["steps"][0]["generation_id"] == first_gen  # shot 1 was kept, not paid for twice


async def test_cancel_stops_the_run_and_its_takes(client, in_memory_queue):
    run = await start(client, "sequence", model_id="kling-3.0", shots=[{"prompt": "a"}, {"prompt": "b"}])
    await worker.tasks["pipeline.advance"].func(run_id=run["id"])  # shot 1 is created but not submitted
    r = await client.post(f"/pipelines/{run['id']}/cancel")
    assert r.json()["status"] == "canceled"
    gen_id = r.json()["steps"][0]["generation_id"]
    async with session_scope() as s:
        assert (await s.get(Generation, uuid.UUID(gen_id))).status.value == "canceled"


async def test_bad_input_is_rejected_before_anything_runs(client):
    r = await client.post("/pipelines", json={"recipe": "sequence", "input": {
        "model_id": "nano-banana-2", "shots": [{"prompt": "x"}]}})
    assert r.status_code == 422 and "needs a video model" in r.json()["errors"][0]
    r = await client.post("/pipelines", json={"recipe": "compare", "input": {
        "prompt": "x", "entries": [{"model_id": "veo-3.1"}, {"model_id": "nano-banana-2"}]}})
    assert r.status_code == 422 and "same kind of output" in r.json()["errors"][0]
    r = await client.post("/pipelines", json={"recipe": "sequence", "input": {"model_id": "veo-3.1", "shots": []}})
    assert r.status_code == 422
    est = await client.post("/pipelines/estimate", json={"recipe": "sequence", "input": {
        "model_id": "veo-3.1", "shots": [{"prompt": "x", "duration": 8}, {"prompt": "y", "duration": 8}]}})
    assert est.status_code == 200
    assert est.json()["generations"] == 2 and est.json()["live_estimate_usd"] == pytest.approx(2 * 3.2)


async def test_revising_a_shot_redoes_only_that_shot_and_the_cut(client, in_memory_queue):
    shots = [{"prompt": "dawn over a harbor"}, {"prompt": "a ferry leaves"}, {"prompt": "gulls follow it"}]
    run = await start(client, "sequence", model_id="kling-3.0", shots=shots)
    await drain(in_memory_queue)
    before = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}

    shots[2]["prompt"] = "a storm rolls in behind it"
    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {"model_id": "kling-3.0", "shots": shots}})
    assert r.status_code == 200 and r.json()["status"] == "running"
    await drain(in_memory_queue)
    after = await get(client, run["id"])
    assert after["status"] == "succeeded"
    steps = {s["key"]: s for s in after["steps"]}
    for key in ("shot[01]", "frame[01]", "shot[02]", "frame[02]"):
        assert steps[key]["output"] == before[key]["output"], key  # untouched
    assert steps["shot[03]"]["generation_id"] != before["shot[03]"]["generation_id"]
    assert steps["cut"]["output"]["asset_id"] != before["cut"]["output"]["asset_id"]
    assert after["output"]["asset_id"] == steps["cut"]["output"]["asset_id"]


async def test_character_shot_renders_a_keyframe_from_references_then_animates_it(client, in_memory_queue):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (512, 512), (30, 90, 160)).save(buf, "PNG")
    up = (await client.post("/uploads", files={"file": ("ref.png", buf.getvalue(), "image/png")})).json()
    name = f"Odile {uuid.uuid4().hex[:6]}"
    el = (await client.post("/elements", json={"name": name, "description": "a dancer in a white dress",
                                               "images": [{"asset_id": up["id"], "view": "front"}]})).json()

    no_mention = await client.post("/pipelines", json={"recipe": "keyframe", "input": {
        "model_id": "kling-3.0", "prompt": "a dancer spins"}})
    assert no_mention.status_code == 422 and "@name" in no_mention.json()["errors"][0]

    run = await start(client, "keyframe", model_id="kling-3.0", prompt=f"{el['handle']} spins on a dark stage",
                      params={"duration": 5, "aspect_ratio": "16:9"})
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        frame_gen = await s.get(Generation, uuid.UUID(steps["keyframe"]["generation_id"]))
        shot_gen = await s.get(Generation, uuid.UUID(steps["shot"]["generation_id"]))
    assert frame_gen.model_id == "nano-banana-2" and frame_gen.mode == "edit"
    assert [m["asset_id"] for m in frame_gen.request["media"]] == [up["id"]]  # the character's picture
    assert frame_gen.request["params"]["aspect_ratio"] == "16:9"
    assert shot_gen.mode == "i2v" and shot_gen.request["media"][0]["asset_id"] == done["output"]["keyframe_asset_id"]
