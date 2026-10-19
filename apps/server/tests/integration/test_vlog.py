"""Character vlogs on the mock provider: Claude's script (mock), the picture as every shot's reference, on-camera
shots lip-synced and cutaways animated, a refused shot falling back to its picture, then the captioned cut."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from test_faceless import answer
from test_pipelines import drain, get, start
from test_talking import PICTURE, upload

from studio.api.main import app as api
from studio.db.models import Generation, PipelineStep
from studio.db.session import session_scope
from studio.pipelines.recipes.vlog import VlogRecipe
from studio.registry.loader import get_registry


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def test_a_picture_and_an_idea_become_a_captioned_vlog(client, in_memory_queue):
    me = await upload(client, PICTURE, "image/png")
    inp = {"character_image": me["id"], "idea": "a rainy Sunday at the flower market", "shots": 4,
           "aspect_ratio": "9:16", "resolution": "720p"}
    est = (await client.post("/pipelines/estimate", json={"recipe": "vlog", "input": inp})).json()
    assert est["generations"] == 4 + 4 + 2 + 2 + 1  # pictures, voices, talking, cutaways, music

    run = await start(client, "vlog", **inp)
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting" and [s["key"] for s in waiting["steps"]] == ["script", "review"]
    shots = waiting["steps"][0]["output"]["shots"]
    assert [s["on_camera"] for s in shots] == [True, False, True, True]

    await answer(client, run["id"], "review", "approve")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert {"lip[s01]", "clip[s02]", "lip[s03]", "lip[s04]", "music", "cut", "captions"} <= set(steps)

    async with session_scope() as s:
        frame = await s.get(Generation, uuid.UUID(steps["frame[s02]"]["generation_id"]))
        lip = await s.get(Generation, uuid.UUID(steps["lip[s01]"]["generation_id"]))
    assert frame and lip
    assert [(m["role"], m["asset_id"]) for m in frame.request["media"]] == [("image_references", me["id"])]
    assert {m["role"]: m["asset_id"] for m in lip.request["media"]} == {
        "character_image": steps["frame[s01]"]["output"]["asset_id"],
        "input_audio": steps["voice[s01]"]["output"]["asset_id"]}
    async with session_scope() as s:
        cut = (await s.execute(select(PipelineStep).where(PipelineStep.run_id == uuid.UUID(run["id"]),
                                                          PipelineStep.key == "cut"))).scalar_one()
    blocks = cut.input["blocks"]
    assert [b.get("hold", False) for b in blocks] == [True, False, True, True]  # lip-synced shots keep their speed

    final = (await client.get(f"/assets/{done['output']['asset_id']}")).json()
    assert (final["width"], final["height"]) == (720, 1280) and final["has_audio"]
    assert done["output"]["captions_asset_id"]


def test_a_refused_shot_falls_back_to_its_picture():
    """The planner is pure: settle every step by hand, with the cutaway refused and a lip sync refused."""
    recipe, reg = VlogRecipe(), get_registry()
    inp = VlogRecipe.Input(character_image=uuid.uuid4(), idea="morning coffee", shots=3, music=False, captions=False,
                           review_script=False)
    shots = [{"line": f"Line {i}.", "visual": f"Shot {i}", "on_camera": i != 2, "motion": ""} for i in (1, 2, 3)]
    settled = {"script": {"title": "Coffee", "shots": shots}}
    for i in (1, 2, 3):
        settled[f"frame[s0{i}]"] = {"asset_id": f"f{i}"}
        settled[f"voice[s0{i}]"] = {"asset_id": f"v{i}", "duration_ms": 3000}
    settled |= {"lip[s01]": {"asset_id": "l1"}, "clip[s02]": {"failed": "moderation"}, "lip[s03]": {"failed": "x"}}
    cut = next(s for s in recipe.plan(inp, settled, reg) if s.key == "cut")
    assert cut.input["blocks"] == [{"visual": "l1", "hold": True, "voice": "v1"}, {"visual": "f2", "voice": "v2"},
                                   {"visual": "f3", "voice": "v3"}]
    assert {"lip[s01]", "clip[s02]", "lip[s03]"} <= set(cut.depends_on)


async def test_models_that_cant_do_their_part_are_refused(client):
    me = await upload(client, PICTURE, "image/png")
    r = await client.post("/pipelines", json={"recipe": "vlog", "input": {
        "character_image": me["id"], "idea": "x", "lipsync_model": "veo-3.1", "video_model": "eleven-v4"}})
    assert r.status_code == 422
    assert "Veo 3.1 can't make a picture speak" in r.text and "can't animate a picture" in r.text
    assert VlogRecipe().validate(VlogRecipe.Input(character_image=uuid.uuid4(), idea="x"), get_registry()) == []
