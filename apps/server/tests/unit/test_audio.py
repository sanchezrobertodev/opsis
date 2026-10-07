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
from studio.providers.base import AmbiguousSubmitError, ProviderError
from studio.providers.elevenlabs import BASE_URL, ElevenLabsAdapter
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.schema import OutputSpec, Pricing, Rate, Route, RouteMode
from studio.registry.validate import normalize

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
