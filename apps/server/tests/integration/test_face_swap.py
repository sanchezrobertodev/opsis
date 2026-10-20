"""Face swap recipe on the mock provider: a picture with an image model, a clip with a person-swap model, a saved
character's face, consent and the models' limits."""

from __future__ import annotations

import uuid

import httpx
import pytest
from test_pipelines import drain, get, start
from test_talking import CLIP, PICTURE, ROOT, upload

from studio.api.main import app as api
from studio.db.models import Generation
from studio.db.session import session_scope

PERSON_5S = ROOT / "fixtures" / "videos" / "person_5s.mp4"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def _swap_generation(client, run_id: str) -> Generation:
    done = await get(client, run_id)
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == ["swap"] and done["output"]["asset_id"]
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(done["steps"][0]["generation_id"]))
    assert gen is not None
    return gen


async def test_a_picture_gets_the_new_face(client, in_memory_queue):
    picture, face = await upload(client, PICTURE, "image/png"), await upload(client, PICTURE, "image/png")
    rendered = (await client.post("/templates/face-swap-picture/render", json={
        "fields": {"consent": "true"}, "media": {"picture_asset_id": picture["id"], "face_asset_id": face["id"]}})).json()
    est = (await client.post("/pipelines/estimate", json=rendered)).json()
    assert est["live_estimate_usd"] == pytest.approx(0.15)  # one Nano Banana Pro edit
    run = await start(client, "face_swap", **rendered["input"])
    await drain(in_memory_queue)
    gen = await _swap_generation(client, run["id"])
    assert gen.model_id == "nano-banana-pro" and "Image 2" in gen.request["prompt"]
    assert gen.request["params"]["aspect_ratio"] == "16:9"  # the keyframe fixture's 1280 x 720, kept
    # the picture first, then the face, as the prompt says
    assert [m["asset_id"] for m in gen.request["media"]] == [picture["id"], face["id"]]


async def test_a_clip_gets_a_saved_characters_face(client, in_memory_queue):
    from test_elements import make_element

    maya = await make_element(client, "Maya Lind")
    clip = await upload(client, CLIP, "video/mp4")
    body = {"video_asset_id": clip["id"], "character": maya["slug"], "consent": True}
    est = (await client.post("/pipelines/estimate", json={"recipe": "face_swap", "input": body})).json()
    assert est["live_estimate_usd"] == pytest.approx(0.20)  # PixVerse Swap at 720p, a clip under 5 s
    run = await start(client, "face_swap", **body)
    await drain(in_memory_queue)
    gen = await _swap_generation(client, run["id"])
    assert gen.model_id == "pixverse-swap"
    assert [(m["role"], m["asset_id"]) for m in gen.request["media"]] == [
        ("input_video", clip["id"]), ("character_image", maya["images"][0]["asset"]["id"])]


async def test_whole_person_and_recast_models_and_their_limits(client, in_memory_queue):
    clip, face = await upload(client, CLIP, "video/mp4"), await upload(client, PICTURE, "image/png")
    run = await start(client, "face_swap", video_asset_id=clip["id"], face_asset_id=face["id"], consent=True,
                      swap_model="wan-2.2-animate")
    await drain(in_memory_queue)
    gen = await _swap_generation(client, run["id"])
    assert gen.mode == "replace" and {m["role"] for m in gen.request["media"]} == {"driving_video", "character_image"}

    # H3 Recast takes clips of 5-30 s: a 3 s one is turned away before anything is spent
    short = await client.post("/pipelines", json={"recipe": "face_swap", "input": {
        "video_asset_id": clip["id"], "face_asset_id": face["id"], "consent": True,
        "swap_model": "minimax-h3-max-recast"}})
    assert short.status_code == 422 and "at least 5 s" in short.text
    long = await upload(client, PERSON_5S, "video/mp4")
    run = await start(client, "face_swap", video_asset_id=long["id"], face_asset_id=face["id"], consent=True,
                      swap_model="minimax-h3-max-recast")
    await drain(in_memory_queue)
    gen = await _swap_generation(client, run["id"])
    assert gen.model_id == "minimax-h3-max-recast" and gen.request["media"][1]["role"] == "image_references"


def test_the_swap_keeps_the_pictures_shape():
    from studio.config import get_settings
    from studio.pipelines.recipes.face_swap import keep_shape
    from studio.registry.loader import load_registry

    spec = load_registry(get_settings().resolve_registry_dir()).models["nano-banana-pro"]
    assert keep_shape(spec, 1080 / 1920) == {"aspect_ratio": "9:16"}
    assert keep_shape(spec, 1.0) == {"aspect_ratio": "1:1"}
    assert keep_shape(spec, 1200 / 1500) == {"aspect_ratio": "4:5"}
    assert keep_shape(spec, None) == {}


async def test_a_video_model_cant_swap_a_picture(client, in_memory_queue):
    picture = await upload(client, PICTURE, "image/png")
    r = await client.post("/pipelines", json={"recipe": "face_swap", "input": {
        "picture_asset_id": picture["id"], "face_asset_id": picture["id"], "consent": True,
        "image_model": "minimax-h3-max"}})
    assert r.status_code == 422 and "can't edit a picture" in r.text


async def test_consent_and_a_face_are_required(client, in_memory_queue):
    from test_elements import make_element

    picture = await upload(client, PICTURE, "image/png")
    no = await client.post("/pipelines", json={"recipe": "face_swap", "input": {
        "picture_asset_id": picture["id"], "face_asset_id": picture["id"]}})
    assert no.status_code == 422 and "consent" in no.text
    faceless = await client.post("/pipelines", json={"recipe": "face_swap", "input": {
        "picture_asset_id": picture["id"], "consent": True}})
    assert faceless.status_code == 422 and "new face" in faceless.text
    nobody = await make_element(client, "No Pictures", images=0)
    r = await client.post("/pipelines", json={"recipe": "face_swap", "input": {
        "picture_asset_id": picture["id"], "character": nobody["slug"], "consent": True}})
    assert r.status_code == 422 and "no picture" in r.text
