"""Audio: ElevenLabs adapter mechanics (respx), word timings, and the audio models' payloads and prices."""

from __future__ import annotations

import base64
import itertools

import httpx
import pytest
import respx

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.media.speech import estimate_words, spoken_seconds, words_from_characters
from studio.providers.base import AmbiguousSubmitError, ProviderError, ResolvedMedia
from studio.providers.elevenlabs import BASE_URL, ElevenLabsAdapter
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.schema import OutputSpec, Pricing, Rate, Route, RouteMode
from studio.registry.validate import MediaRef, normalize

REG = load_registry(get_settings().resolve_registry_dir())
GEORGE = "JBFqnCBsd6RMkjVDRZzb"


def route() -> Route:
    return Route(id="a@elevenlabs", provider="elevenlabs", modes={"tts": RouteMode(endpoint="x")},
                 pricing=Pricing(basis="per_request", rates=[Rate(usd=0)]), output=OutputSpec(path="audio", kind="audio"))


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "test-key")
    a = ElevenLabsAdapter()
    a.store.root = tmp_path / "results"
    return a


# ---------------------------------------------------------------- word timings


def test_words_from_characters_groups_on_spaces():
    chars = list("Hi there.")
    starts = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    ends = [s + 0.1 for s in starts]
    assert words_from_characters(chars, starts, ends) == [
        {"text": "Hi", "start": 0.0, "end": 0.2},
        {"text": "there.", "start": 0.3, "end": 0.9},
    ]


def test_estimated_words_are_ordered_and_pause_at_punctuation():
    words = estimate_words("One two. Three")
    assert [w["text"] for w in words] == ["One", "two.", "Three"]
    assert all(a["end"] <= b["start"] for a, b in itertools.pairwise(words))
    assert words[2]["start"] - words[1]["end"] >= 0.3  # the full stop
    assert spoken_seconds(words) == pytest.approx(words[-1]["end"] + 0.25)
    assert spoken_seconds([]) == 0.0


# ---------------------------------------------------------------- adapter


@respx.mock
async def test_tts_with_timestamps_parks_audio_and_alignment(adapter, tmp_path):
    body = {"audio_base64": base64.b64encode(b"ID3fake").decode(),
            "alignment": {"characters": list("Go now"), "character_start_times_seconds": [0, .1, .2, .3, .4, .5],
                          "character_end_times_seconds": [.1, .2, .3, .4, .5, .6]}}
    call = respx.post(f"{BASE_URL}/text-to-speech/{GEORGE}/with-timestamps").respond(
        200, json=body, headers={"request-id": "r1", "x-character-count": "6"})
    res = await adapter.submit(route(), f"text-to-speech/{GEORGE}/with-timestamps",
                               {"text": "Go now", "model_id": "eleven_v4"}, client_ref="c")
    sent = call.calls.last.request
    assert sent.headers["xi-api-key"] == "test-key" and sent.url.params["output_format"] == "mp3_44100_128"
    polled = await adapter.poll(route(), res.job_id)
    assert polled.status == "succeeded" and polled.usage == {"characters": 6}
    out = polled.outputs[0]
    assert out.kind == "audio"
    assert [w["text"] for w in out.meta["alignment"]["words"]] == ["Go", "now"]
    dest = tmp_path / "a.mp3"
    assert await adapter.download(route(), out, dest, tmp_path) == "audio/mpeg"
    assert dest.read_bytes() == b"ID3fake"


@respx.mock
async def test_sound_effects_return_raw_audio(adapter):
    respx.post(f"{BASE_URL}/sound-generation").respond(200, content=b"ID3sfx", headers={"content-type": "audio/mpeg"})
    res = await adapter.submit(route(), "sound-generation", {"text": "door creak", "duration_seconds": 3}, client_ref="c")
    out = (await adapter.poll(route(), res.job_id)).outputs[0]
    assert out.meta == {}


@respx.mock
async def test_errors_and_timeouts(adapter):
    respx.post(f"{BASE_URL}/music").respond(401, json={"detail": {"status": "invalid_api_key"}})
    with pytest.raises(ProviderError) as err:
        await adapter.submit(route(), "music", {"prompt": "x"}, client_ref="c")
    assert err.value.error_class == ErrorClass.route_unavailable
    respx.post(f"{BASE_URL}/sound-generation").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousSubmitError):
        await adapter.submit(route(), "sound-generation", {"text": "x"}, client_ref="c")


async def test_no_key_is_route_unavailable(monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    with pytest.raises(ProviderError) as err:
        await ElevenLabsAdapter().submit(route(), "music", {}, client_ref="c")
    assert err.value.error_class == ErrorClass.route_unavailable


# ---------------------------------------------------------------- registry payloads and prices


def payload(model_id: str, mode: str, prompt: str, params: dict):
    spec = REG.model(model_id)
    req = normalize(spec, mode, prompt, params, [])
    route_ = next(r for r in spec.routes if r.provider == "elevenlabs")
    return build_payload(spec, route_, req, {}), req


def test_eleven_v4_payload_puts_the_voice_in_the_path():
    (endpoint, p, _), _ = payload("eleven-v4", "tts", "Hello there.", {"tier": "turbo", "speed": 1.1})
    assert endpoint == f"text-to-speech/{GEORGE}/with-timestamps"
    assert p["text"] == "Hello there." and p["model_id"] == "eleven_v4_turbo" and "voice_id" not in p
    assert p["voice_settings"] == {"speed": 1.1, "stability": 0.5} and "tier" not in p


def test_eleven_v4_prices_by_characters():
    spec = REG.model("eleven-v4")
    pricing = spec.route("eleven-v4@elevenlabs").pricing
    assert float(estimate(pricing, {"tier": "standard"}, "x" * 2500).usd) == pytest.approx(0.20)
    assert float(estimate(pricing, {"tier": "turbo"}, "x" * 2500).usd) == pytest.approx(0.10)


def test_music_and_sfx_payloads():
    (endpoint, p, _), _ = payload("eleven-music", "music", "calm piano", {"duration": 45})
    assert endpoint == "music"
    assert p == {"prompt": "calm piano", "music_length_ms": 45000, "force_instrumental": True, "model_id": "music_v1"}
    (endpoint, p, _), _req = payload("eleven-sound-effects", "sfx", "thunder", {"duration": 4})
    assert endpoint == "sound-generation" and p["text"] == "thunder" and p["duration_seconds"] == 4
    pricing = REG.model("eleven-music").route("eleven-music@elevenlabs").pricing
    assert float(estimate(pricing, {"duration": 60}, "x").usd) == pytest.approx(0.15)


def test_voice_labels_cover_known_options():
    voice = REG.model("eleven-v4").param("voice_id")
    assert set(voice.labels) == set(voice.options)


# ---------------------------------------------------------------- the voice changer and voice cloning


def test_a_cloned_voice_id_passes_where_a_voice_is_picked():
    spec = REG.model("eleven-voice-changer")
    out = normalize(spec, "change", "", {"voice_id": "a1B2c3CustomVoice"},
                    [MediaRef(role="input_audio", asset_id="a", kind="audio")])
    assert out.params["voice_id"] == "a1B2c3CustomVoice"  # voice_id is open: the account's own voices count
    with pytest.raises(Exception, match="must be one of"):  # a closed list still refuses
        normalize(REG.model("eleven-v4"), "tts", "hi", {"tier": "ultra"}, [])


@respx.mock
async def test_the_voice_changer_uploads_the_recording(adapter, tmp_path):
    rec = tmp_path / "line.mp3"
    rec.write_bytes(b"ID3recording")
    staged = await adapter.stage_inputs(route(), [ResolvedMedia("input_audio", "a", rec, "audio/mpeg", "audio")])
    spec = REG.model("eleven-voice-changer")
    req = normalize(spec, "change", "", {"voice_id": "cgSgspJ2msm6clMCkdW9", "remove_background_noise": True},
                    [MediaRef(role="input_audio", asset_id="a", kind="audio")])
    endpoint, payload, _ = build_payload(spec, spec.route("eleven-voice-changer@elevenlabs"), req, staged)
    assert endpoint == "speech-to-speech/cgSgspJ2msm6clMCkdW9"
    call = respx.post(f"{BASE_URL}/{endpoint}").respond(200, content=b"ID3changed", headers={"content-type": "audio/mpeg"})
    res = await adapter.submit(route(), endpoint, payload, client_ref="c")
    body = call.calls.last.request.content
    assert b'name="audio"; filename="line.mp3"' in body and b"ID3recording" in body
    assert b'name="model_id"' in body and b"eleven_multilingual_sts_v2" in body
    assert b'name="remove_background_noise"' in body and b"true" in body
    assert (await adapter.poll(route(), res.job_id)).status == "succeeded"
    with pytest.raises(ProviderError, match="audio files"):
        await adapter.stage_inputs(route(), [ResolvedMedia("input_audio", "a", rec, "video/mp4", "video")])


@respx.mock
async def test_cloning_and_deleting_a_voice(monkeypatch, tmp_path):
    from studio.providers import elevenlabs

    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "test-key")
    sample = tmp_path / "me.mp3"
    sample.write_bytes(b"ID3me")
    add = respx.post(f"{BASE_URL}/voices/add").respond(200, json={"voice_id": "vNEW", "requires_verification": False})
    assert await elevenlabs.clone_voice("Me", [sample], remove_background_noise=False) == "vNEW"
    sent = add.calls.last.request.content
    assert b'name="name"' in sent and b"Me" in sent and b'name="files"; filename="me.mp3"' in sent
    gone = respx.delete(f"{BASE_URL}/voices/vNEW").respond(200, json={"status": "ok"})
    await elevenlabs.delete_voice("vNEW")
    assert gone.called


@respx.mock
async def test_a_prompt_that_looks_like_a_path_is_never_read(adapter, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not send")
    call = respx.post(f"{BASE_URL}/text-to-speech/{GEORGE}/with-timestamps").respond(
        200, json={"audio_base64": base64.b64encode(b"ID3").decode()})
    for text in (f"file://{secret}", f"staged-file:{'0' * 32}"):  # not a token this adapter issued
        await adapter.submit(route(), f"text-to-speech/{GEORGE}/with-timestamps", {"text": text}, client_ref="c")
        sent = call.calls.last.request
        assert sent.headers["content-type"] == "application/json" and b"do not send" not in sent.content


def dub_route() -> Route:
    return REG.models["eleven-dubbing"].routes[0]


@respx.mock
async def test_a_dub_is_started_followed_and_fetched_with_a_fresh_link(adapter, tmp_path):
    rec = tmp_path / "line.mp3"
    rec.write_bytes(b"ID3 speech")
    staged = await adapter.stage_inputs(dub_route(), [ResolvedMedia("input_audio", "a", rec, "audio/mpeg", "audio")])
    create = respx.post(f"{BASE_URL}/dubbing/project").respond(201, json={"project_id": "proj_1", "status": "queued"})
    sub = await adapter.submit(dub_route(), "dubbing/project", {"file": staged["input_audio"][0], "target_language": "es",
                                                                "source_language": "auto", "model_id": "dubbing_v2"},
                               client_ref="c")
    sent = create.calls.last.request
    assert sub.job_id == "dub:proj_1" and b"ID3 speech" in sent.content and b"target_language" in sent.content
    assert b"source_language" not in sent.content and "output_format" not in str(sent.url)  # auto is left out

    project = respx.get(f"{BASE_URL}/dubbing/project/proj_1")
    language = respx.get(f"{BASE_URL}/dubbing/project/proj_1/language/lang_1")
    project.respond(200, json={"project_id": "proj_1", "status": "preparing", "language_ids": []})
    assert (await adapter.poll(dub_route(), sub.job_id)).status == "running"
    project.respond(200, json={"project_id": "proj_1", "status": "ready", "language_ids": ["lang_1"],
                               "source_language": "en", "media": {"duration": 3.0}})
    language.respond(200, json={"status": "processing", "target_language": "es"})
    assert (await adapter.poll(dub_route(), sub.job_id)).status == "running"
    language.respond(200, json={"status": "completed", "target_language": "es",
                                "outputs": {"lossless_audio": "https://files.test/old.flac"}})
    done = await adapter.poll(dub_route(), sub.job_id)
    assert done.status == "succeeded" and done.outputs[0].meta["language"] == "es" and done.usage == {"seconds": 3.0}

    language.respond(200, json={"status": "completed", "outputs": {"lossless_audio": "https://files.test/new.flac"}})
    respx.get("https://files.test/new.flac").respond(200, content=b"fLaC", headers={"content-type": "audio/flac"})
    dest = tmp_path / "out.flac"
    assert await adapter.download(dub_route(), done.outputs[0], dest, tmp_path) == "audio/flac"
    assert dest.read_bytes() == b"fLaC"


@respx.mock
async def test_a_failed_dub_says_why(adapter):
    respx.get(f"{BASE_URL}/dubbing/project/proj_2").respond(200, json={
        "status": "failed", "language_ids": [], "error": {"message_type": "error", "error": "no speech found"}})
    r = await adapter.poll(dub_route(), "dub:proj_2")
    assert r.status == "failed" and "no speech found" in (r.error_message or "")


@respx.mock
async def test_uploads_are_streamed_not_read_whole(adapter, tmp_path, monkeypatch):
    import anyio

    rec = tmp_path / "long.mp3"
    rec.write_bytes(b"ID3" + b"x" * 300_000)

    async def no_whole_reads(self):
        raise AssertionError("an upload was read into memory whole")

    monkeypatch.setattr(anyio.Path, "read_bytes", no_whole_reads)
    staged = await adapter.stage_inputs(dub_route(), [ResolvedMedia("input_audio", "a", rec, "audio/mpeg", "audio")])
    create = respx.post(f"{BASE_URL}/dubbing/project").respond(201, json={"project_id": "proj_3"})
    await adapter.submit(dub_route(), "dubbing/project", {"file": staged["input_audio"][0], "target_language": "es"},
                         client_ref="c")
    assert len(create.calls.last.request.content) > 300_000  # the whole file still went up


def test_a_voice_id_cant_rewrite_the_elevenlabs_address():
    from studio.registry.mapping import endpoint_for

    assert endpoint_for("text-to-speech/{voice_id}/with-timestamps", {"voice_id": GEORGE}) == \
        f"text-to-speech/{GEORGE}/with-timestamps"
    for sneaky in ("../../voices/abc/edit?x=", "..", "."):
        url = endpoint_for("text-to-speech/{voice_id}/with-timestamps", {"voice_id": sneaky})
        segment = url.removeprefix("text-to-speech/").removesuffix("/with-timestamps")
        assert "/" not in segment and "?" not in segment and segment not in (".", ".."), url
        assert str(httpx.URL(f"https://api.test/v1/{url}").path).startswith("/v1/text-to-speech/")
