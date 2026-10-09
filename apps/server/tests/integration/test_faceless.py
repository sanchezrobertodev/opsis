"""Faceless videos end to end on the mock provider and mock Claude: script, approval, plan, narration with word
timings, stills or clips, music, assembly, captions and thumbnails, through the real engine and ffmpeg."""

from __future__ import annotations

import uuid

import httpx
import pytest
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Asset
from studio.db.session import session_scope
from studio.media.ffmpeg import probe
from studio.storage.local import get_storage


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def answer(client, run_id: str, key: str, choice: str) -> None:
    r = await client.post(f"/pipelines/{run_id}/checkpoints/{key}", json={"choice": choice})
    assert r.status_code == 200, r.text


def steps_of(run: dict) -> dict[str, dict]:
    return {s["key"]: s for s in run["steps"]}


async def test_stills_episode_from_a_topic(client, in_memory_queue):
    run = await start(client, "faceless", topic="The keeper of the last lighthouse", theme="storytelling",
                      style="poster-vector", minutes=0.5, resolution="720p")
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    steps = steps_of(waiting)
    assert waiting["status"] == "waiting" and steps["review"]["status"] == "waiting"
    script = steps["script"]["output"]["script"]
    assert script.startswith("This is the story of the keeper of the last lighthouse.")

    # editing the script before approving re-asks for approval of the new text and skips the writer
    edited = "The keeper climbed the stairs. She lit the lamp for the last time. Then she went home."
    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {**waiting["input"], "script": edited,
                                                                          "title": "The last lamp"}})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    steps = steps_of(await get(client, run["id"]))
    assert steps["review"]["status"] == "waiting" and "plan" not in steps  # finished work is kept, nothing reruns

    await answer(client, run["id"], "review", "approve")
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    steps = steps_of(waiting)
    assert waiting["status"] == "waiting" and "thumb" in steps, [(k, s["status"], s["error"]) for k, s in steps.items()]
    assert len(steps["thumb"]["options"]) == 3
    blocks = steps["plan"]["output"]["blocks"]
    assert " ".join(b["narration"] for b in blocks) == edited
    voices = [steps[f"voice[b{i:02d}]"] for i in range(1, len(blocks) + 1)]
    assert all(v["status"] == "succeeded" for v in voices) and "clip[b01]" not in steps

    await answer(client, run["id"], "thumb", steps["thumb"]["options"][1]["value"])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    out = done["output"]
    assert out["thumbnail_asset_id"] == steps["thumb"]["options"][1]["value"]

    async with session_scope() as s:
        voice = await s.get(Asset, uuid.UUID(voices[0]["output"]["asset_id"]))
        words = voice.metadata_["alignment"]["words"]
        assert words[0]["text"] == "The" and voice.duration_ms > 1000
        final = await s.get(Asset, uuid.UUID(out["asset_id"]))
        master = await s.get(Asset, uuid.UUID(out["master_asset_id"]))
        srt = await s.get(Asset, uuid.UUID(out["captions_asset_id"]))
    assert srt.kind.value == "subtitle"
    text = get_storage().path_for(srt.storage_key).read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in text and "keeper" in text
    info = await probe(get_storage().path_for(final.storage_key))
    expected = sum(v["output"]["duration_ms"] for v in voices) / 1000 + 0.35 * (len(voices) - 1) + 1.2
    assert info.width == 1280 and info.height == 720 and info.has_audio
    assert info.duration_ms / 1000 == pytest.approx(expected, abs=0.3)
    assert master.duration_ms == pytest.approx(final.duration_ms, abs=150)


async def test_motion_episode_from_my_script_animates_each_still(client, in_memory_queue):
    script = "Bees scout for a new home. They dance to vote. The swarm moves when they agree."
    run = await start(client, "faceless", script=script, title="How bees vote", format="motion", theme="education",
                      style="colorful-3d", aspect_ratio="9:16", resolution="720p", review_script=False, music=False,
                      thumbnail=False)
    assert run["estimate_usd"] == 0
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = steps_of(done)
    assert "script" not in steps and "review" not in steps and "music" not in steps
    n = len(steps["plan"]["output"]["blocks"])
    for i in range(1, n + 1):
        clip = steps[f"clip[b{i:02d}]"]
        assert clip["status"] == "succeeded"
        async with session_scope() as s:
            from studio.db.models import Generation

            gen = await s.get(Generation, uuid.UUID(clip["generation_id"]))
        assert [(m["role"], m["asset_id"]) for m in gen.request["media"]] == [
            ("start_image", steps[f"still[b{i:02d}]"]["output"]["asset_id"])]
    async with session_scope() as s:
        final = await s.get(Asset, uuid.UUID(done["output"]["asset_id"]))
    assert (final.width, final.height) == (720, 1280)


async def test_estimate_counts_narration_and_validation_errors(client):
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {"topic": "x", "style": "nope"}})
    assert r.status_code == 422 and "unknown style" in r.text
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {"style": "hand-drawn"}})
    assert r.status_code == 422 and "topic" in r.text
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "x", "minutes": 1, "format": "motion"}})
    assert r.status_code == 200, r.text
    est = r.json()
    # 1 minute in 8 s blocks: 8 narrations + 8 stills + 8 clips + music + thumbnails
    assert est["generations"] == 26


async def test_catalog_topics_and_channels(client, in_memory_queue):
    cat = (await client.get("/faceless/catalog")).json()
    assert len(cat["themes"]) == 4 and len(cat["styles"]) == 12
    assert cat["voices"][0] == {"id": "JBFqnCBsd6RMkjVDRZzb", "label": "George: warm British storyteller"}
    assert cat["defaults"]["format"] == "stills" and "topic" not in cat["defaults"]

    r = await client.post("/faceless/topics", json={"theme": "history"})
    assert r.status_code == 200 and r.json()["mock"] and len(r.json()["topics"]) == 4

    bad = await client.post("/faceless/channels", json={"name": "x", "theme": "history", "style": "nope",
                                                        "defaults": {"topic": "t", "format": "sideways"}})
    assert bad.status_code == 422 and "unknown style" in bad.text and "not an episode setting: topic" in bad.text
    r = await client.post("/faceless/channels", json={
        "name": "Lost Lighthouses", "theme": "history", "style": "watercolor-chronicle",
        "notes": "Maritime history only.", "defaults": {"format": "motion", "aspect_ratio": "9:16"}})
    assert r.status_code == 201, r.text
    ch = r.json()
    assert ch["defaults"] == {"format": "motion", "aspect_ratio": "9:16"} and ch["episodes"] == 0

    run = await start(client, "faceless", channel_id=ch["id"], topic="The Bell Rock light", theme="history",
                      style="watercolor-chronicle", review_script=False, thumbnail=False, music=False,
                      captions=False, minutes=0.5, resolution="720p")
    listed = (await client.get("/faceless/channels")).json()
    assert listed[0]["id"] == ch["id"] and listed[0]["episodes"] == 1
    episodes = (await client.get(f"/pipelines?channel_id={ch['id']}")).json()
    assert [e["id"] for e in episodes] == [run["id"]]
    assert (await client.get("/pipelines?recipe=sequence")).json() == []

    r = await client.patch(f"/faceless/channels/{ch['id']}", json={"name": "Lighthouses", "defaults": {"music": False}})
    assert r.json()["name"] == "Lighthouses" and r.json()["defaults"] == {"music": False}
    assert (await client.delete(f"/faceless/channels/{ch['id']}")).status_code == 204
    assert (await client.get(f"/faceless/channels/{ch['id']}")).status_code == 404
    await drain(in_memory_queue)
    assert (await get(client, run["id"]))["status"] == "succeeded"


async def test_channel_counts_only_its_owners_episodes(client, in_memory_queue):
    from studio.db.models import PipelineRun, User

    ch = (await client.post("/faceless/channels", json={"name": "Mine", "theme": "kids", "style": "colorful-3d"})).json()
    async with session_scope() as s:
        other = User(display_name="someone else")
        s.add(other)
        await s.flush()
        s.add(PipelineRun(user_id=other.id, recipe="faceless", title="not mine", input={"channel_id": ch["id"]}))
    listed = {c["id"]: c for c in (await client.get("/faceless/channels")).json()}
    assert listed[ch["id"]]["episodes"] == 0


async def test_kids_song_is_sung_section_by_section_under_its_pictures(client, in_memory_queue):
    run = await start(client, "faceless", topic="lighthouses", theme="kids", style="poster-vector", format="song",
                      minutes=0.5, resolution="720p", thumbnail=False)
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    steps = steps_of(waiting)
    assert steps["script"]["label"] == "Write the song" and steps["review"]["status"] == "waiting"
    lyrics = steps["script"]["output"]["song"]
    assert [s["name"] for s in lyrics["sections"]] == ["Verse 1", "Chorus", "Verse 2", "Chorus"]
    assert steps["review"]["output"] == {} and "[Chorus]" in steps["script"]["output"]["script"]

    await answer(client, run["id"], "review", "approve")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    steps = steps_of(done)
    assert done["status"] == "succeeded", [(k, s["status"], s["error"]) for k, s in steps.items()]
    assert "voice[b01]" not in steps and "plan" not in steps  # no narration in a song
    async with session_scope() as s:
        from studio.db.models import Generation

        sing = await s.get(Generation, uuid.UUID(steps["sing"]["generation_id"]))
        final = await s.get(Asset, uuid.UUID(done["output"]["asset_id"]))
        srt = await s.get(Asset, uuid.UUID(done["output"]["captions_asset_id"]))
    assert sing.mode == "song" and [x["duration"] for x in sing.request["params"]["sections"]] == [10, 10, 10, 10]
    assert sing.request["params"]["duration"] == 40
    assert len(steps["parts"]["output"]["asset_ids"]) == 4
    info = await probe(get_storage().path_for(final.storage_key))
    assert info.has_audio and info.duration_ms / 1000 == pytest.approx(40 + 0.5, abs=0.4)  # back to back, no gaps
    assert steps["cut"]["output"]["starts"] == [0.0, 10.0, 20.0, 30.0]
    text = get_storage().path_for(srt.storage_key).read_text(encoding="utf-8")
    assert "Have you heard of lighthouses?" in text and "00:00:02," in text  # sung after a bar's lead-in


async def test_my_own_lyrics_make_the_sections():
    from studio.faceless.writer import section_seconds, song_from_text, song_text

    song = song_from_text("Twinkle little star\nHow I wonder what you are\n\n[Chorus]\nShine, shine\nAll night", "Star")
    assert [(s.name, s.lines) for s in song.sections] == [
        ("Verse 1", ["Twinkle little star", "How I wonder what you are"]), ("Chorus", ["Shine, shine", "All night"])]
    assert section_seconds(song.sections[0].lines) == 10.0 and section_seconds([]) == 3.0
    assert song_text(song).startswith("[Verse 1]\nTwinkle little star")
