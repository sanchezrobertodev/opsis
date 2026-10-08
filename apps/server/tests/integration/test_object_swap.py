"""Object swap end to end on the mock provider: click-to-segment on a frame, track the mask through the clip,
edit with several models, composite each through the matte, and pick the take."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Asset, AssetLineage, Generation
from studio.db.session import session_scope
from studio.media.ffmpeg import probe
from studio.storage.local import get_storage

ROOT = Path(__file__).resolve().parents[4]
CLIP = ROOT / "fixtures" / "videos" / "clip_3s_720p.mp4"  # Kling O3 Edit takes 720p and up
CLICK = [[0.5, 0.5, 1]]  # the click that made the mask; the tracker needs it as well as the mask


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def generate(client, queue, body: dict) -> dict:
    r = await client.post("/generations", json=body)
    assert r.status_code in (200, 201, 202), r.text
    await drain(queue)
    done = (await client.get(f"/generations/{r.json()['id']}")).json()
    assert done["status"] == "succeeded", done
    return done["assets"][0]


async def selected_object(client, queue) -> tuple[dict, dict]:
    video = (await client.post("/uploads", files={"file": ("clip.mp4", CLIP.read_bytes(), "video/mp4")})).json()
    frame = (await client.post(f"/assets/{video['id']}/frames", json={"at": 1.0})).json()
    mask = await generate(client, queue, {"model_id": "sam-3-image", "mode": "segment", "prompt": "",
                                          "params": {"points": [[0.5, 0.5, 1], [0.9, 0.1, 0]]},
                                          "media": [{"role": "input_image", "asset_id": frame["id"]}]})
    return video, mask


async def test_segment_needs_a_click_and_makes_a_mask(client, in_memory_queue):
    video, mask = await selected_object(client, in_memory_queue)
    assert mask["kind"] == "mask" and (mask["width"], mask["height"]) == (video["width"], video["height"])
    r = await client.post("/generations", json={"model_id": "sam-3-image", "mode": "segment", "prompt": "",
                                                "params": {"points": [[0.5, 0.5, 0]]},
                                                "media": [{"role": "input_image", "asset_id": mask["id"]}]})
    assert r.status_code == 422 and "Click on the object" in r.text and "input_image must be image" in r.text


async def test_swap_with_two_models_keeps_the_background_and_waits_for_a_pick(client, in_memory_queue):
    video, mask = await selected_object(client, in_memory_queue)
    run = await start(client, "object_swap", video_asset_id=video["id"], mask_asset_id=mask["id"], frame_index=24,
                      points=[[0.4, 0.6, 1]], prompt="Replace the mug with a small potted cactus",
                      models=[{"model_id": "kling-o3-edit"}, {"model_id": "runway-aleph-2"}])
    assert run["estimate_usd"] == 0
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    steps = {s["key"]: s for s in waiting["steps"]}
    assert waiting["status"] == "waiting", waiting["error"]
    assert set(steps) == {"track", "edit[1:kling-o3-edit]", "keep[1:kling-o3-edit]", "edit[2:runway-aleph-2]",
                          "keep[2:runway-aleph-2]", "pick"}
    matte_id = steps["track"]["output"]["asset_id"]

    async with session_scope() as s:
        track = await s.get(Generation, uuid.UUID(steps["track"]["generation_id"]))
        edit = await s.get(Generation, uuid.UUID(steps["edit[1:kling-o3-edit]"]["generation_id"]))
        matte = await s.get(Asset, uuid.UUID(matte_id))
    assert track.request["params"]["frame_index"] == 24 and matte.kind.value == "mask_video"
    assert track.request["params"]["points"] == [[0.4, 0.6, 1]]  # the tracker starts from the clicks too
    assert edit.request["prompt"].startswith("Replace the mug with a small potted cactus. Change only that object.")
    assert [m["role"] for m in edit.request["media"]] == ["input_video"]  # Kling O3 takes no mask

    choice = steps["pick"]["options"][1]["value"]
    assert choice == steps["keep[2:runway-aleph-2]"]["output"]["asset_id"]
    r = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": choice})
    assert r.status_code == 200
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded" and done["output"]["asset_id"] == choice
    assert done["output"]["matte_asset_id"] == matte_id
    assert all(t["raw_asset_id"] and t["asset_id"] != t["raw_asset_id"] for t in done["output"]["takes"])

    async with session_scope() as s:
        merged = await s.get(Asset, uuid.UUID(choice))
        relations = {r.relation for r in (await s.execute(
            select(AssetLineage).where(AssetLineage.child_asset_id == merged.id))).scalars()}
    assert relations == {"original", "edited", "matte"}
    info = await probe(get_storage().path_for(merged.storage_key))
    assert (info.width, info.height) == (video["width"], video["height"])
    assert info.duration_ms == pytest.approx(video["duration_ms"], abs=100)


async def test_remove_with_one_model_and_a_mask_review(client, in_memory_queue):
    video, mask = await selected_object(client, in_memory_queue)
    run = await start(client, "object_swap", video_asset_id=video["id"], mask_asset_id=mask["id"], action="remove", points=CLICK,
                      prompt="the mug", review_mask=True, keep_background=False,
                      models=[{"model_id": "seedance-2.5"}])
    await drain(in_memory_queue)
    steps = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}
    assert steps["mask"]["status"] == "waiting" and "edit[1:seedance-2.5]" not in steps
    await client.post(f"/pipelines/{run['id']}/checkpoints/mask", json={"choice": "approve"})
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    take = done["output"]["takes"][0]
    assert done["output"]["asset_id"] == take["raw_asset_id"]  # no composite: the edit as it came
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(next(x for x in done["steps"] if x["key"].startswith("edit"))["generation_id"]))
    assert gen.mode == "video_edit" and gen.request["prompt"].startswith("Remove the mug from the video")


async def test_swap_validation(client):
    body = {"video_asset_id": str(uuid.uuid4()), "mask_asset_id": str(uuid.uuid4()),
            "models": [{"model_id": "veo-3.1"}, {"model_id": "kling-o3-edit"}, {"model_id": "kling-o3-edit"}]}
    r = await client.post("/pipelines/estimate", json={"recipe": "object_swap", "input": body})
    assert r.status_code == 422
    for problem in ("say what the object should become", "Veo 3.1 can't replace objects in a clip",
                    "pick each model once"):
        assert problem in r.text


async def test_a_run_where_every_model_fails_is_a_failed_run(client, in_memory_queue):
    video, mask = await selected_object(client, in_memory_queue)
    run = await start(client, "object_swap", video_asset_id=video["id"], mask_asset_id=mask["id"], points=CLICK,
                      prompt="Make it gold [[mock:fail=moderation]]",
                      models=[{"model_id": "kling-o3-edit"}, {"model_id": "runway-aleph-2"}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "failed" and done["error"].startswith("No model produced a take.")
    assert "kling-o3-edit:" in done["error"] and "runway-aleph-2:" in done["error"]


async def test_too_long_a_clip_is_refused_before_anything_runs(client, in_memory_queue, tmp_path):
    from studio.media.mock_media import make_video

    clip = tmp_path / "long.mp4"
    await make_video(clip, tmp_path / "w", model_id="m", mode="x", prompt="long", duration=16, resolution="360p",
                     aspect="16:9")
    video = (await client.post("/uploads", files={"file": ("long.mp4", clip.read_bytes(), "video/mp4")})).json()
    _, mask = await selected_object(client, in_memory_queue)
    r = await client.post("/pipelines/estimate", json={"recipe": "object_swap", "input": {
        "video_asset_id": video["id"], "mask_asset_id": mask["id"], "prompt": "a cactus", "points": CLICK,
        "models": [{"model_id": "kling-o3-edit"}]}})
    assert r.status_code == 422 and "at most 15 s" in r.text


async def test_a_region_edit_of_a_cinema_take_becomes_a_take_of_its_shot(client, in_memory_queue):
    video, mask = await selected_object(client, in_memory_queue)
    project = (await client.post("/projects", json={"title": "Harbour", "document": {}})).json()
    run = await start(client, "object_swap", video_asset_id=video["id"], mask_asset_id=mask["id"], frame_index=24,
                      points=CLICK, prompt="Make the mug a brass lantern", models=[{"model_id": "kling-o3-edit"}],
                      project_id=project["id"], shot_key="sone")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    takes = (await client.get(f"/projects/{project['id']}/takes", params={"shot_key": "sone"})).json()
    assert [(t["made_by"], t["parent_asset_id"]) for t in takes] == [("region_edit", video["id"])]
    assert takes[0]["asset"]["id"] == done["output"]["asset_id"]


async def test_live_tracking_masks_are_stored_as_a_matte_clip(client, in_memory_queue):
    # what fal's sam-3/video-rle answers (one run-length mask per frame) becomes a mask_video asset at ingest
    from studio.db.models import GenerationAttempt
    from studio.domain.enums import AttemptStatus, GenStatus
    from studio.worker.tasks.generation import run_ingest

    video, mask = await selected_object(client, in_memory_queue)
    w, h = video["width"], video["height"]
    square = " ".join(f"{row * w + 600} 80" for row in range(320, 400))  # an 80 x 80 square at (600, 320)
    media = [{"role": "input_video", "asset_id": video["id"], "kind": "video", "width": w, "height": h,
              "fps": video["fps"], "duration_ms": video["duration_ms"]},
             {"role": "mask", "asset_id": mask["id"], "kind": "mask", "width": w, "height": h}]
    async with session_scope() as s:
        user = (await s.get(Asset, uuid.UUID(video["id"]))).user_id
        gen = Generation(user_id=user, model_id="sam-3-video", mode="track", output_type="mask_video",
                         status=GenStatus.ingesting, request={"model_id": "sam-3-video", "mode": "track", "prompt": "",
                                                              "params": {"points": CLICK}, "media": media})
        s.add(gen)
        await s.flush()
        attempt = GenerationAttempt(generation_id=gen.id, attempt_no=1, route_id="sam-3-video@fal", provider="fal",
                                    status=AttemptStatus.succeeded,
                                    last_response={"outputs": [{"index": 0, "kind": "mask_video",
                                                                "data": [square] * 24}], "usage": {}})
        s.add(attempt)
        await s.commit()
        gen_id, attempt_id = gen.id, attempt.id
    await run_ingest(attempt_id)
    async with session_scope() as s:
        matte = (await s.execute(select(Asset).where(Asset.generation_id == gen_id))).scalar_one()
        done = await s.get(Generation, gen_id)
    assert done.status == GenStatus.succeeded
    assert matte.kind.value == "mask_video" and (matte.width, matte.height) == (w, h)
    info = await probe(get_storage().path_for(matte.storage_key))
    assert info.duration_ms == pytest.approx(24 / video["fps"] * 1000, abs=60)
