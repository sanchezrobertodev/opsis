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
from studio.llm.pricing import typical_cost
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
    writing = typical_cost("faceless.script", "claude-opus-5-5") + typical_cost("faceless.plan", "claude-opus-5-5")
    assert est["claude_usd"] == 0 and est["claude_live_usd"] == float(writing)
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "x", "minutes": 1, "format": "motion", "script": "My own words."}})
    assert r.json()["claude_live_usd"] == float(typical_cost("faceless.plan", "claude-opus-5-5"))  # no script to write


async def test_catalog_topics_and_channels(client, in_memory_queue):
    cat = (await client.get("/faceless/catalog")).json()
    assert len(cat["themes"]) == 5 and len(cat["styles"]) == 13
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
    cues = [c.split("\n", 2) for c in text.strip().split("\n\n")]
    assert cues[0][1] == "00:00:02,000 --> 00:00:06,000" and cues[0][2] == "Have you heard of lighthouses?"
    assert [c[2] for c in cues[2:4]] == ["Sing about lighthouses", "Clap your hands along"]  # a line each
    assert cues[2][1].startswith("00:00:12,000")  # the chorus starts after its own bar in, never mid-cue


def test_song_captions_give_each_line_its_share_of_its_section():
    from studio.media.captions import line_cues

    out = line_cues([(2.0, 10.0, ["Round and round, my light says, \"Hey!", "Sail this way!"]), (12.0, 20.0, [])],
                    max_chars=30)
    assert [(c.start, c.end) for c in out] == [(2.0, 6.0), (6.0, 10.0)]
    assert out[0].text == "Round and round, my light\nsays, \"Hey!" and out[1].text == "Sail this way!"
    assert out[1].words[0]["start"] == 6.0 and out[1].words[-1]["end"] == 10.0


async def test_my_own_lyrics_make_the_sections():
    from studio.faceless.writer import section_seconds, song_from_text, song_text

    song = song_from_text("Twinkle little star\nHow I wonder what you are\n\n[Chorus]\nShine, shine\nAll night", "Star")
    assert [(s.name, s.lines) for s in song.sections] == [
        ("Verse 1", ["Twinkle little star", "How I wonder what you are"]), ("Chorus", ["Shine, shine", "All night"])]
    assert section_seconds(song.sections[0].lines) == 10.0 and section_seconds([]) == 3.0
    assert song_text(song).startswith("[Verse 1]\nTwinkle little star")


async def test_the_longest_song_stays_within_the_music_models_limits(client):
    from studio.faceless.writer import fit_song, song_lines

    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "the moon", "theme": "kids", "format": "song", "minutes": 5, "thumbnail": False}})
    assert r.status_code == 200, r.text
    assert r.json()["generations"] == 13  # 12 pictures (the most sections) and the song
    assert song_lines(5) == 66 and song_lines(0.5) == 6
    fitted = fit_song([60.0] * 6, 300)
    assert sum(fitted) <= 300 and fitted == [50.0] * 6
    assert fit_song([10.0, 20.0], 300) == [10.0, 20.0]


async def test_a_creature_documentary_stars_a_saved_creature_and_keeps_the_clips_sound(client, in_memory_queue):
    """The creature template's run: a saved creature drawn from its pictures in the shots Claude marks, the
    thumbnail too, and clips made with their own sound that the cut keeps under the narration."""
    from test_elements import make_element

    from studio.db.models import Generation
    from studio.media.assemble import CLIP_SOUND_DB

    griffin = await make_element(client, "Griffin", description="a lion's body with an eagle's head and wings")
    rendered = await client.post("/templates/creature-documentary/render", json={
        "fields": {"topic": "A griffin defends its cliff nest", "footage": "kling-3.0", "shape": "9:16"},
        "media": {"character": griffin["slug"]}})
    assert rendered.status_code == 200, rendered.text
    inp = {**rendered.json()["input"], "music": False, "captions": False, "review_script": False, "minutes": 0.5}
    assert inp["clip_sound"] and inp["format"] == "motion" and inp["character"] == griffin["slug"]
    run = await start(client, "faceless", **inp)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    if done["status"] == "waiting":  # the thumbnail pick
        await answer(client, run["id"], "thumb", steps_of(done)["thumb"]["options"][0]["value"])
        await drain(in_memory_queue)
        done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = steps_of(done)
    blocks = steps["plan"]["output"]["blocks"]
    assert any(b["star"] for b in blocks) and not all(b["star"] for b in blocks)  # mock: every other shot

    async def gen_of(step: dict) -> Generation:
        async with session_scope() as s:
            g = await s.get(Generation, uuid.UUID(step["generation_id"]))
        assert g is not None
        return g

    picture = griffin["images"][0]["asset"]["id"]
    for i, block in enumerate(blocks, 1):
        still = await gen_of(steps[f"still[b{i:02d}]"])
        if block["star"]:  # drawn from the griffin's own picture, named in words, no @ left
            assert still.mode == "edit" and [m["asset_id"] for m in still.request["media"]] == [picture]
            assert "Griffin" in still.request["compiled_prompt"] and "@" not in still.request["compiled_prompt"]
        else:
            assert still.mode == "t2i" and still.request["media"] == []
        clip = await gen_of(steps[f"clip[b{i:02d}]"])
        assert clip.request["params"]["generate_audio"] is True and "Natural sound of the scene" in clip.request["prompt"]
        if block["star"]:  # the video model is told what it is, by name and look, not "the star"
            assert "Griffin (a lion's body with an eagle's head and wings)" in clip.request["prompt"]
            assert "the star" not in clip.request["prompt"]
    thumbs = await gen_of(steps["thumbs"])
    assert thumbs.mode == "edit" and picture in [m["asset_id"] for m in thumbs.request["media"]]
    async with session_scope() as s:
        from sqlalchemy import select

        from studio.db.models import PipelineStep

        cut = (await s.execute(select(PipelineStep).where(
            PipelineStep.run_id == uuid.UUID(run["id"]), PipelineStep.key == "cut"))).scalar_one()
    assert cut.input["clip_sound_db"] == CLIP_SOUND_DB


async def test_a_star_needs_an_image_model_that_draws_from_pictures(client):
    from test_elements import make_element

    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "a wendigo hunts", "character": "wendigo"}})
    assert r.status_code == 422 and "no saved character called @wendigo" in r.text  # named, before anything is spent
    wendigo = await make_element(client, "Wendigo")
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "a wendigo hunts", "character": wendigo["slug"], "image_model": "flux-2-klein-4b"}})
    assert r.status_code == 422 and "reference pictures" in r.text
    # a song draws no star yet, and stills have no sound of their own: both are simply left out
    r = await client.post("/pipelines/estimate", json={"recipe": "faceless", "input": {
        "topic": "x", "theme": "kids", "format": "song", "character": "wendigo", "clip_sound": True}})
    assert r.status_code == 200, r.text


async def test_the_creature_template_runs_without_a_saved_creature(client):
    r = await client.post("/templates/creature-documentary/render", json={"fields": {"topic": "Thunderbirds"}})
    assert r.status_code == 200, r.text
    inp = r.json()["input"]
    assert "character" not in inp and inp["aspect_ratio"] == "9:16" and inp["video_model"] == "kling-3.0"
    est = await client.post("/pipelines/estimate", json=r.json())
    assert est.status_code == 200, est.text


async def test_the_creature_template_asks_for_its_length_and_narrator(client):
    """A length is held to (and the script written for it); the narrator defaults to David and can be any voice."""
    from studio.pipelines.recipes.faceless import FacelessInput

    default = (await client.post("/templates/creature-documentary/render", json={"fields": {"topic": "Kraken"}})).json()
    inp = FacelessInput.model_validate(default["input"])
    assert (inp.max_seconds, inp.minutes, inp.voice["voice_id"]) == (60, 1.0, "BNgbHR0DNeZixGQVzloa")
    short = (await client.post("/templates/creature-documentary/render", json={"fields": {
        "topic": "Kraken", "length": "30", "narrator": "JBFqnCBsd6RMkjVDRZzb"}})).json()
    inp = FacelessInput.model_validate(short["input"])
    assert (inp.max_seconds, inp.minutes, inp.voice["voice_id"]) == (30, 0.5, "JBFqnCBsd6RMkjVDRZzb")
    assert FacelessInput(topic="x", minutes=2, max_seconds=30).minutes == 2  # a length given is kept
    est = await client.post("/pipelines/estimate", json=short)
    assert est.status_code == 200, est.text


async def test_a_plan_with_a_star_always_shows_it_and_never_describes_it(client, monkeypatch):
    """Claude forgetting to mark the star's shots (or listing its look) can't leave the creature out or fight its
    pictures: every shot shows it, and its roster entry is dropped."""
    from studio.config import get_settings
    from studio.db.session import session_scope
    from studio.faceless import writer
    from studio.faceless.catalog import get_catalog

    original = writer._mock_plan

    def forgetful(script, fmt, star=""):
        plan = original(script, fmt)  # no star flags at all
        plan.roster += [writer.RosterItem(name="Griffin", kind="character", look="a lion with eagle wings")]
        return plan

    def half_flagged(script, fmt, star=""):
        plan = original(script, fmt, star)
        plan.blocks[1].visual = "Close-up of the star's talons gripping the rock"  # named, but not flagged
        plan.blocks[1].star = False
        return plan

    monkeypatch.setattr(writer, "_mock_plan", forgetful)
    async with session_scope() as s:
        res = await writer.plan_blocks(s, get_settings().owner_user_id, script="It waits. It strikes. It eats.",
                                       title="Griffin", style=get_catalog().style("wildlife-documentary"),
                                       fmt="motion", star="Griffin")
    assert res.output.blocks and all(b.star for b in res.output.blocks)
    assert all(r.name != "Griffin" for r in res.output.roster) and any(r.name == "the keeper" for r in res.output.roster)

    monkeypatch.setattr(writer, "_mock_plan", half_flagged)
    async with session_scope() as s:
        long = " ".join(f"At first light the griffin watches the valley from its ledge, still as stone {n}." for n in range(8))
        res = await writer.plan_blocks(s, get_settings().owner_user_id, script=long,
                                       title="Griffin", style=get_catalog().style("wildlife-documentary"),
                                       fmt="motion", star="Griffin")
    assert res.output.blocks[1].star  # a shot that names the star shows it


def test_clip_sound_needs_a_model_that_makes_sound_and_a_star_name_is_kept_as_written():
    from studio.pipelines.recipes.faceless import FacelessInput, FacelessRecipe, named
    from studio.registry.loader import get_registry

    reg = get_registry()
    silent = FacelessInput(topic="a wendigo hunts", format="motion", clip_sound=True, video_model="runway-gen-4-turbo")
    assert any("without sound" in e for e in FacelessRecipe().validate(silent, reg))
    assert not any("without sound" in e for e in FacelessRecipe().validate(silent.model_copy(update={"clip_sound": False}), reg))
    always = silent.model_copy(update={"video_model": "minimax-h3-max"})  # sound included, no switch
    assert not any("without sound" in e for e in FacelessRecipe().validate(always, reg))
    assert named("Close-up of the star waiting", "Creature \\1") == "Close-up of Creature \\1 waiting"
    assert named("The star lands; the star folds its wings.", "Griffin", "a lion with eagle wings.") == (
        "Griffin (a lion with eagle wings) lands; Griffin folds its wings.")  # described once, then named


STORY = " ".join(f"The griffin circles the ridge for the {n} time, watching the ravens below the nest." for n in
                 ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"))


async def test_a_longest_length_stops_before_the_clips_and_the_voices_pace_is_measured(client, in_memory_queue):
    """With a longest length set, the clips wait for every narration; an episode that runs over stops with its exact
    length before any clip is paid for, and goes on when told to. Its narrations then measure the voice's pace."""
    from studio.config import get_settings
    from studio.faceless.writer import WORDS_PER_MINUTE
    from studio.media.assemble import timeline
    from studio.pipelines.llm_ops import voice_pace

    run = await start(client, "faceless", script=STORY, format="motion", max_seconds=10, music=False, captions=False,
                      thumbnail=False, review_script=False, voice={"voice_id": "BNgbHR0DNeZixGQVzloa"})
    await drain(in_memory_queue)
    held = await get(client, run["id"])
    steps = steps_of(held)
    assert held["status"] == "waiting" and steps["length"]["status"] == "waiting"
    assert not any(k.startswith("clip[") for k in steps) and "cut" not in steps
    voiced = [s["output"]["duration_ms"] / 1000 for k, s in sorted(steps.items()) if k.startswith("voice[")]
    assert "over your 10 s" in steps["length"]["label"] and f"{timeline(voiced).total:.1f} s" in steps["length"]["label"]

    await answer(client, run["id"], "length", "approve")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    async with session_scope() as s:  # the length it stopped with is the episode's length
        cut = await s.get(Asset, uuid.UUID(steps_of(done)["cut"]["output"]["asset_id"]))
        assert cut is not None
    info = await probe(get_storage().path_for(cut.storage_key))
    assert abs((info.duration_ms or 0) / 1000 - timeline(voiced).total) < 0.1

    async with session_scope() as s:
        wpm = await voice_pace(s, get_settings().owner_user_id, "eleven-v4", "BNgbHR0DNeZixGQVzloa")
        other = await voice_pace(s, get_settings().owner_user_id, "eleven-v4", "a-voice-never-heard")
    assert wpm != WORDS_PER_MINUTE and 60 < wpm < 400 and other == WORDS_PER_MINUTE

    under = await start(client, "faceless", script=STORY, format="motion", max_seconds=600, music=False,
                        captions=False, thumbnail=False, review_script=False)
    await drain(in_memory_queue)
    done = await get(client, under["id"])
    assert done["status"] == "succeeded" and "length" not in steps_of(done)


async def test_a_script_fits_its_length_after_the_pauses_and_is_shortened_when_long(monkeypatch):
    from studio.config import get_settings
    from studio.faceless import writer
    from studio.faceless.catalog import get_catalog

    # the pauses the cut adds come off: half a minute at 150 a minute is fewer than 75 words, a slower voice fewer still
    assert writer.target_words(0.5, "motion", 138) < writer.target_words(0.5, "motion") < 75
    calls: list[int] = []
    original = writer._mock_script

    def wordy(topic, words):
        calls.append(words)
        return original(topic, words * 2 if len(calls) == 1 else words)  # the first draft runs twice as long

    monkeypatch.setattr(writer, "_mock_script", wordy)
    async with session_scope() as s:
        res = await writer.write_script(s, get_settings().owner_user_id, topic="A griffin", minutes=0.5, wpm=138,
                                        theme=get_catalog().theme("creature-documentary"))
    target = writer.target_words(0.5, "motion", 138)
    assert len(calls) == 2 and len(res.output.narration.split()) <= target * writer.OVER_BY


async def test_a_reference_sheet_is_drawn_from_the_characters_pictures_and_attaches_next(client, in_memory_queue):
    """The sheet is drawn in the image model's reference mode from the character's pictures, kept right after its
    main picture, so a mention attaches the main picture and the sheet (a character attaches two)."""
    from test_elements import make_element

    from studio.config import get_settings
    from studio.director.elements import resolve_mentions
    from studio.registry.loader import get_registry

    griffin = await make_element(client, "Griffin", description="a lion with eagle wings", images=2)
    pictures = [i["asset"]["id"] for i in griffin["images"]]
    r = await client.post("/pipelines/estimate", json={"recipe": "character_sheet", "input": {
        "element_id": griffin["id"], "slug": "nobody-here"}})
    assert r.status_code == 422 and "no saved character called @nobody-here" in r.text
    run = await start(client, "character_sheet", element_id=griffin["id"], slug=griffin["slug"])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    sheet = done["output"]["asset_id"]
    async with session_scope() as s:
        from studio.db.models import Generation

        drawn = await s.get(Generation, uuid.UUID(steps_of(done)["sheet"]["generation_id"]))
        assert drawn is not None and drawn.mode == "edit"
        assert [m["asset_id"] for m in drawn.request["media"]] == pictures  # from its own pictures
        spec = get_registry().models["nano-banana-2"]
        res = await resolve_mentions(s, get_settings().owner_user_id, spec, "edit", f"@{griffin['slug']} lands", [])
    assert [m["asset_id"] for m in res.media] == [pictures[0], sheet]


async def test_more_pictures_of_a_character_vary_and_join_its_pictures(client, in_memory_queue):
    """Each picture is drawn from the character's own pictures with its own angle, expression and light, and kept
    after the ones it had, so a trained identity gets the varied photos it needs."""
    griffin = await make_griffin(client)
    r = await client.post("/pipelines/estimate", json={"recipe": "character_photos", "input": {
        "element_id": griffin["id"], "slug": griffin["slug"], "count": 3}})
    assert r.status_code == 200 and r.json()["generations"] == 3, r.text
    run = await start(client, "character_photos", element_id=griffin["id"], slug=griffin["slug"], count=3,
                      vary=["angle", "expression"])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    made = done["output"]["asset_ids"]
    assert len(made) == 3
    async with session_scope() as s:
        from studio.db.models import Generation

        drawn = [await s.get(Generation, uuid.UUID(st["generation_id"]))
                 for k, st in steps_of(done).items() if k.startswith("pic[")]
    prompts = [g.request["prompt"] for g in drawn if g is not None]
    assert len(set(prompts)) == 3 and all("even studio light" in p for p in prompts)  # light wasn't varied
    assert {g.mode for g in drawn if g is not None} == {"edit"}  # drawn from its pictures
    pictures = [i["asset"]["id"] for i in (await client.get(f"/elements/{griffin['id']}")).json()["images"]]
    assert pictures[:2] == [i["asset"]["id"] for i in griffin["images"]] and pictures[2:] == made

    # had they failed, a retry would draw them again from the pictures the @mention attaches (none in their media)
    async with session_scope() as s:
        from sqlalchemy import update

        from studio.db.models import PipelineStep, StepStatus

        await s.execute(update(PipelineStep).where(PipelineStep.run_id == uuid.UUID(run["id"]),
                                                   PipelineStep.key.like("pic[%")).values(status=StepStatus.failed))
    redo = await client.get(f"/pipelines/{run['id']}/retry/estimate")
    assert redo.status_code == 200 and redo.json()["generations"] == 3, redo.text


async def make_griffin(client):
    from test_elements import make_element

    return await make_element(client, "Griffin", description="a lion with eagle wings", images=2)
