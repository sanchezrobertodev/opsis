"""Music videos on the mock provider: the song transcribed (the mock hears a "la" every half second), cut into shots
at its breaths, a shot per section drawn (from the star's picture when there is one) and animated, then joined over
the song with the lyrics as word-timed captions."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from test_pipelines import drain, get, start
from test_talking import PICTURE, upload

from studio.api.main import app as api
from studio.db.models import Asset, Generation, PipelineStep
from studio.db.session import session_scope
from studio.media.mock_media import make_audio
from studio.pipelines.recipes.music_video import MusicVideoRecipe
from studio.registry.loader import get_registry
from studio.storage.local import get_storage


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def a_song(client, tmp_path, seconds: float = 20.0) -> dict:
    path = tmp_path / "song.mp3"
    await make_audio(path, tmp_path / "work", duration=seconds)
    r = await client.post("/uploads", files={"file": ("song.mp3", path.read_bytes(), "audio/mpeg")})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def step_input(run_id: str, key: str) -> dict:
    async with session_scope() as s:
        return (await s.execute(select(PipelineStep.input).where(
            PipelineStep.run_id == uuid.UUID(run_id), PipelineStep.key == key))).scalar_one()


async def test_a_song_and_a_star_become_a_captioned_music_video(client, in_memory_queue, tmp_path):
    song, star = await a_song(client, tmp_path), await upload(client, PICTURE, "image/png")
    inp = {"song": song["id"], "character_image": star["id"], "idea": "a night drive", "resolution": "720p"}
    est = (await client.post("/pipelines/estimate", json={"recipe": "music_video", "input": inp})).json()
    assert est["generations"] == 1 + 24 + 24  # transcript, then a three-minute song's pictures and clips

    run = await start(client, "music_video", **inp)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}

    # the transcript is a JSON asset of timed words
    async with session_scope() as s:
        transcript = await s.get(Asset, uuid.UUID(steps["transcribe"]["output"]["asset_id"]))
        assert transcript and transcript.kind == "subtitle"
        words = json.loads(get_storage().path_for(transcript.storage_key).read_text())["words"]
    assert words and {w["text"] for w in words} == {"la"}

    shots = steps["shots"]["output"]["shots"]
    assert 3 <= len(shots) <= 4 and shots[-1]["end"] == pytest.approx(song["duration_ms"] / 1000, abs=0.05)
    assert {f"frame[s{i:02d}]" for i in range(1, len(shots) + 1)} <= set(steps)
    async with session_scope() as s:
        frame = await s.get(Generation, uuid.UUID(steps["frame[s01]"]["generation_id"]))
    assert frame and [(m["role"], m["asset_id"]) for m in frame.request["media"]] == [("image_references", star["id"])]
    assert "not a collage" in frame.request["prompt"]  # one moment per picture, never panels

    cut = await step_input(run["id"], "cut")
    assert len(cut["blocks"]) == len(shots) and cut["gap_s"] == 0  # the song plays through, unbroken
    captions = await step_input(run["id"], "captions")
    assert captions["words"] == [s["words"] for s in shots]

    final = (await client.get(f"/assets/{done['output']['asset_id']}")).json()
    assert (final["width"], final["height"]) == (720, 1280) and final["has_audio"]
    assert final["duration_ms"] == pytest.approx(song["duration_ms"], abs=300)


async def test_without_a_star_shots_are_drawn_from_text(client, in_memory_queue, tmp_path):
    song = await a_song(client, tmp_path, seconds=8.0)
    run = await start(client, "music_video", song=song["id"], captions=False)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert "captions" not in steps and len(steps["shots"]["output"]["shots"]) == 1
    async with session_scope() as s:
        frame = await s.get(Generation, uuid.UUID(steps["frame[s01]"]["generation_id"]))
    assert frame and frame.mode == "t2i" and frame.request["media"] == []


def test_an_instrumental_gets_no_captions_and_a_refused_clip_shows_its_picture():
    recipe, reg = MusicVideoRecipe(), get_registry()
    inp = MusicVideoRecipe.Input(song=uuid.uuid4())
    sections = [{"start": 0.0, "end": 6.0, "text": "", "words": []}, {"start": 6.0, "end": 12.0, "text": "", "words": []}]
    settled = {"transcribe": {"asset_id": "t"}, "shots": {"shots": sections, "seconds": [6.0, 6.0], "lyrics": False},
               "plan": {"world": "w", "shots": [{"visual": "a", "motion": "m"}, {"visual": "b", "motion": "m"}]},
               "parts": {"asset_ids": ["p1", "p2"], "asset_id": "p1"},
               "frame[s01]": {"asset_id": "f1"}, "frame[s02]": {"asset_id": "f2"},
               "clip[s01]": {"asset_id": "c1"}, "clip[s02]": {"failed": "moderation"},
               "cut": {"asset_id": "cut", "starts": [0, 6]}}
    keys = {s.key: s for s in recipe.plan(inp, settled, reg)}
    assert "captions" not in keys
    assert keys["cut"].input["blocks"] == [{"visual": "c1", "voice": "p1"}, {"visual": "f2", "voice": "p2"}]
    assert recipe.result(inp, settled) == {"asset_id": "cut", "master_asset_id": "cut", "captions_asset_id": None}


async def test_models_that_cant_do_their_part_are_refused(client, tmp_path):
    song = await a_song(client, tmp_path, seconds=4.0)
    r = await client.post("/pipelines", json={"recipe": "music_video", "input": {
        "song": song["id"], "transcribe_model": "eleven-v4", "video_model": "nano-banana-2"}})
    assert r.status_code == 422
    assert "can't hear the lyrics" in r.text and "Nano Banana 2 can't animate a picture" in r.text


async def test_a_saved_character_stars_in_the_music_video(client, in_memory_queue, tmp_path):
    from test_elements import make_element

    star = await make_element(client, "Maya Lind")
    song = await a_song(client, tmp_path, seconds=8.0)
    run = await start(client, "music_video", song=song["id"], character=star["slug"], captions=False)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        frame = await s.get(Generation, uuid.UUID(steps["frame[s01]"]["generation_id"]))
    assert frame and [m["asset_id"] for m in frame.request["media"]] == [star["images"][0]["asset"]["id"]]
    assert "Maya Lind" in frame.request["compiled_prompt"]


async def test_a_brief_becomes_a_sung_song_and_its_music_video(client, in_memory_queue):
    from test_faceless import answer

    inp = {"brief": "two friends driving to the coast", "style": "indie pop", "minutes": 0.5, "captions": False}
    est = (await client.post("/pipelines/estimate", json={"recipe": "music_video", "input": inp})).json()
    assert est["generations"] == 1 + 1 + 5 + 5  # the song, its transcript, then 30 s of six-second shots

    run = await start(client, "music_video", **inp)
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting" and [s["key"] for s in waiting["steps"]] == ["lyrics", "review"]
    await answer(client, run["id"], "review", "approve")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    sung = steps["sing"]["output"]["asset_id"]
    async with session_scope() as s:
        sing = await s.get(Generation, uuid.UUID(steps["sing"]["generation_id"]))
        heard = await s.get(Generation, uuid.UUID(steps["transcribe"]["generation_id"]))
    assert sing and sing.mode == "song" and "indie pop" in sing.request["prompt"]
    assert heard and [m["asset_id"] for m in heard.request["media"]] == [sung]  # the new song is what's cut
    assert (await step_input(run["id"], "parts"))["audio"] == sung

    both = await client.post("/pipelines", json={"recipe": "music_video", "input": {
        "song": str(uuid.uuid4()), "brief": "x"}})
    assert both.status_code == 422 and "not both" in both.text
    too_long = await client.post("/pipelines", json={"recipe": "music_video", "input": {
        "brief": "x", "minutes": 1.5, "max_shots": 4}})
    assert too_long.status_code == 422 and "4 shots cover at most 40 s" in too_long.text
    # a song Claude writes longer than asked is sung no longer than its shots can cover
    recipe = MusicVideoRecipe()
    long_inp = MusicVideoRecipe.Input(brief="x", minutes=0.5, max_shots=4)
    sung = recipe._sing_params(long_inp, get_registry(), [["la"] * 8] * 4)
    assert sum(x["duration"] for x in sung["sections"]) <= 40
