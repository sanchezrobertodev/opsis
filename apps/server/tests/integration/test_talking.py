"""Talking shots on the mock provider: a script voiced with TTS and lip-synced onto a picture or a clip, a revised
script redoing only the voice and the lipsync, and a comparison across models that waits for a pick."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Generation
from studio.db.session import session_scope

ROOT = Path(__file__).resolve().parents[4]
PICTURE = ROOT / "fixtures" / "images" / "keyframe.png"
CLIP = ROOT / "fixtures" / "videos" / "clip_3s.mp4"
SPEECH = ROOT / "fixtures" / "audio" / "line.mp3"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def upload(client, path: Path, mime: str) -> dict:
    r = await client.post("/uploads", files={"file": (path.name, path.read_bytes(), mime)})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def generation(step: dict) -> Generation:
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(step["generation_id"]))
    assert gen is not None
    return gen


async def test_a_picture_speaks_a_script_and_a_new_script_redoes_only_voice_and_sync(client, in_memory_queue):
    picture = await upload(client, PICTURE, "image/png")
    script = "The lamp has burned every night for forty years. Tonight it goes dark."
    est = await client.post("/pipelines/estimate", json={"recipe": "talking_shot", "input": {
        "image_asset_id": picture["id"], "script": script, "models": [{"model_id": "kling-avatar-2"}]}})
    assert est.status_code == 200 and est.json()["generations"] == 2

    run = await start(client, "talking_shot", image_asset_id=picture["id"], script=script,
                      voice={"voice_id": "XrExE9yKIg1WjnnlVkGX"}, models=[{"model_id": "kling-avatar-2"}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"voice", "sync[1:kling-avatar-2]"}
    voice, sync = await generation(steps["voice"]), await generation(steps["sync[1:kling-avatar-2]"])
    assert voice.request["params"]["voice_id"] == "XrExE9yKIg1WjnnlVkGX"
    voiced = steps["voice"]["output"]["asset_id"]
    assert {m["role"]: m["asset_id"] for m in sync.request["media"]} == {"character_image": picture["id"],
                                                                           "input_audio": voiced}
    # the take runs as long as the speech, with the speech on it
    take = (await client.get(f"/assets/{done['output']['asset_id']}")).json()
    speech = (await client.get(f"/assets/{voiced}")).json()
    assert abs(take["duration_ms"] - speech["duration_ms"]) < 200 and take["has_audio"]
    assert done["output"]["audio_asset_id"] == voiced

    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {
        "image_asset_id": picture["id"], "script": "Tonight it goes dark.", "voice": {"voice_id": "XrExE9yKIg1WjnnlVkGX"},
        "models": [{"model_id": "kling-avatar-2"}]}})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    redone = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}
    assert redone["voice"]["generation_id"] != steps["voice"]["generation_id"]
    assert redone["sync[1:kling-avatar-2]"]["generation_id"] != steps["sync[1:kling-avatar-2]"]["generation_id"]


async def test_a_clip_lip_synced_to_recorded_speech(client, in_memory_queue):
    clip, speech = await upload(client, CLIP, "video/mp4"), await upload(client, SPEECH, "audio/mpeg")
    run = await start(client, "talking_shot", video_asset_id=clip["id"], audio_asset_id=speech["id"],
                      models=[{"model_id": "sync-lipsync-3", "params": {"sync_mode": "loop"}}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == ["sync[1:sync-lipsync-3]"]  # no voice step for recorded speech

    # a model that only animates pictures can't take a clip
    bad = await client.post("/pipelines", json={"recipe": "talking_shot", "input": {
        "video_asset_id": clip["id"], "audio_asset_id": speech["id"], "models": [{"model_id": "veed-fabric-1.0"}]}})
    assert bad.status_code == 422 and "VEED Fabric 1.0 can't make a clip speak" in bad.text
    both = await client.post("/pipelines", json={"recipe": "talking_shot", "input": {
        "video_asset_id": clip["id"], "script": "Hi", "audio_asset_id": speech["id"],
        "models": [{"model_id": "sync-lipsync-3"}]}})
    assert both.status_code == 422 and "a script or recorded speech" in both.text


async def test_comparing_models_waits_for_a_pick(client, in_memory_queue):
    picture = await upload(client, PICTURE, "image/png")
    run = await start(client, "talking_shot", image_asset_id=picture["id"], script="Hello there.",
                      models=[{"model_id": "kling-avatar-2"}, {"model_id": "infinitetalk"}])
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting", waiting["error"]
    steps = {s["key"]: s for s in waiting["steps"]}
    infinite = await generation(steps["sync[2:infinitetalk]"])
    assert infinite.request["prompt"] == "A person speaking naturally to camera."  # it needs a description
    choice = steps["sync[2:infinitetalk]"]["output"]["asset_id"]
    r = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": choice})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded" and done["output"]["asset_id"] == choice
