"""Voice tools on the mock provider: cloning a voice (a stand-in in mock mode), using it anywhere a voice is picked,
and re-voicing a clip or a recording in it."""

from __future__ import annotations

import uuid

import httpx
import pytest
from test_pipelines import drain, get, start
from test_talking import CLIP, SPEECH, upload

from studio.api.main import app as api
from studio.db.models import Generation
from studio.db.session import session_scope
from studio.media.ffmpeg import ffmpeg


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def test_a_cloned_voice_is_kept_and_speaks_anywhere(client, in_memory_queue):
    sample = await upload(client, SPEECH, "audio/mpeg")
    no = await client.post("/elements/clone-voice", json={"name": "My voice", "samples": [sample["id"]], "consent": False})
    assert no.status_code == 422  # consent is required
    r = await client.post("/elements/clone-voice", json={"name": "My voice", "samples": [sample["id"]], "consent": True})
    assert r.status_code == 201, r.text
    voice = r.json()
    assert voice["kind"] == "voice" and voice["voice"]["params"]["voice_id"].startswith("mock-voice-")
    vid = voice["voice"]["params"]["voice_id"]

    # its id works for text to speech, though it isn't one of the listed voices
    tts = await client.post("/generations", json={"model_id": "eleven-v4", "mode": "tts", "prompt": "Hello there.",
                                                  "params": {"voice_id": vid}})
    assert tts.status_code == 201, tts.text

    # and in a template's voice field
    rendered = await client.post("/templates/change-the-voice/render", json={
        "fields": {"voice": vid}, "media": {"audio_asset_id": sample["id"]}})
    assert rendered.status_code == 200 and rendered.json()["input"]["voice_id"] == vid
    assert (await client.delete(f"/elements/{voice['id']}")).status_code == 204  # mock voices skip the service


async def test_a_clip_is_revoiced_with_its_picture_kept(client, in_memory_queue, tmp_path):
    talking = tmp_path / "talking.mp4"  # a clip with sound (the fixture clip is silent)
    await ffmpeg("-i", str(CLIP), "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-map", "0:v", "-map", "1:a",
                 "-c:v", "copy", "-c:a", "aac", "-shortest", str(talking))
    clip = await upload(client, talking, "video/mp4")
    est = (await client.post("/pipelines/estimate", json={"recipe": "revoice", "input": {
        "video_asset_id": clip["id"], "voice_id": "cgSgspJ2msm6clMCkdW9"}})).json()
    assert est["live_estimate_usd"] == pytest.approx(clip["duration_ms"] / 1000 * 0.002, abs=0.001)  # priced by the clip
    run = await start(client, "revoice", video_asset_id=clip["id"], voice_id="cgSgspJ2msm6clMCkdW9")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == ["audio", "change", "mux"]
    change = next(s for s in done["steps"] if s["key"] == "change")
    async with session_scope() as s:
        gen = await s.get(Generation, uuid.UUID(change["generation_id"]))
    assert gen and gen.request["params"]["voice_id"] == "cgSgspJ2msm6clMCkdW9"
    out = (await client.get(f"/assets/{done['output']['asset_id']}")).json()
    assert out["kind"] == "video" and out["has_audio"]
    assert abs(out["duration_ms"] - clip["duration_ms"]) < 200  # the clip's length, with the new voice under it


async def test_a_recording_is_revoiced_and_priced_by_its_length(client, in_memory_queue):
    rec = await upload(client, SPEECH, "audio/mpeg")
    body = {"recipe": "revoice", "input": {"audio_asset_id": rec["id"], "voice_id": "cgSgspJ2msm6clMCkdW9"}}
    est = (await client.post("/pipelines/estimate", json=body)).json()
    assert est["live_estimate_usd"] == pytest.approx(rec["duration_ms"] / 1000 * 0.002, abs=0.001)
    run = await start(client, "revoice", **body["input"])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded" and [s["key"] for s in done["steps"]] == ["change"]
    both = await client.post("/pipelines", json={"recipe": "revoice", "input": {
        "audio_asset_id": rec["id"], "video_asset_id": rec["id"], "voice_id": "x"}})
    assert both.status_code == 422 and "not both" in both.text


async def test_only_a_voice_the_clone_endpoint_made_is_removed_from_the_service(client, monkeypatch):
    import respx

    from studio.config import get_settings
    from studio.providers.elevenlabs import BASE_URL

    s = get_settings()
    monkeypatch.setattr(s, "elevenlabs_api_key", "test-key")
    sample = await upload(client, SPEECH, "audio/mpeg")
    with respx.mock(assert_all_called=False) as service:
        add = service.post(f"{BASE_URL}/voices/add").respond(200, json={"voice_id": "vREAL"})
        remove = service.delete(url__regex=rf"{BASE_URL}/voices/.*").respond(200, json={})

        # mixed mode with nothing ElevenLabs allowlisted: a stand-in, nothing sent
        monkeypatch.setattr(s, "provider_mode", "mixed")
        monkeypatch.setattr(s, "live_route_allowlist", "")
        mixed = (await client.post("/elements/clone-voice", json={
            "name": "Mixed", "samples": [sample["id"]], "consent": True})).json()
        assert mixed["voice"]["params"]["voice_id"].startswith("mock-voice-") and not add.called

        # live: the real clone, which deleting removes from the service
        monkeypatch.setattr(s, "provider_mode", "live")
        real = (await client.post("/elements/clone-voice", json={
            "name": "Real", "samples": [sample["id"]], "consent": True})).json()
        assert real["voice"]["params"]["voice_id"] == "vREAL" and add.called

        # an element dressed up as a clone of someone else's voice never reaches the service
        fake = (await client.post("/elements", json={"kind": "voice", "name": "Fake", "attributes": {"cloned": "elevenlabs"},
                                                     "voice": {"params": {"voice_id": "victimVoice"}}})).json()
        assert (await client.delete(f"/elements/{fake['id']}")).status_code == 204
        assert not remove.called
        assert (await client.delete(f"/elements/{real['id']}")).status_code == 204
        assert remove.call_count == 1 and remove.calls.last.request.url.path.endswith("/voices/vREAL")
    await client.patch("/budget", json={"provider_mode": "mock"})


async def test_clone_samples_are_size_capped(client, monkeypatch):
    from studio.api.routers import elements

    sample = await upload(client, SPEECH, "audio/mpeg")
    monkeypatch.setattr(elements, "SAMPLE_MAX_BYTES", 10)
    r = await client.post("/elements/clone-voice", json={"name": "Big", "samples": [sample["id"]], "consent": True})
    assert r.status_code == 422 and "keep each sample under" in r.text
