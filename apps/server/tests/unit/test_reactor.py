"""Reactor session lifecycle and spending bounds; SDK/HTTP fakes never start a paid session."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers import reactor as r
from studio.providers.base import AmbiguousSubmitError, ProviderError, ResolvedMedia
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.routing import RoutingEnv, candidates
from studio.registry.validate import MediaRef, normalize

REG = load_registry(get_settings().resolve_registry_dir())
SPEC = REG.model("fast-h3-omni")
ROUTE = SPEC.routes[0]
PAYLOAD = {"prompt": "A pottery wheel hums", "seconds": 5.875, "session_seconds": 60, "aspect_ratio": "16:9"}


class FakeReactor:
    def __init__(self, model, *, jwt):
        assert model == r.MODEL and jwt == "test-jwt"
        self.handlers = {}
        self.calls = []
        self.session_id = "session-1"
        self.failure = None
        self.enqueued = None
        self.closed = False

    def on(self, event, callback):
        self.handlers[event] = callback

    def emit(self, event, clip_id="clip-1"):
        self.handlers["message"]({"type": event, "data": {"clip": {"clip_id": clip_id}}})

    async def connect(self):
        self.calls.append("connect")
        self.handlers["session_id_changed"](self.session_id)
        if self.failure == "connect":
            raise ConnectionError("test-secret must not appear")

    async def upload_file(self, path):
        self.calls.append("upload")
        return SimpleNamespace(file=Path(path).name)  # must remain an object, not a stringified path

    async def send_command(self, command, data):
        self.calls.append(command)
        if self.failure == command:
            raise RuntimeError("test-secret must not appear")
        if command == "enqueue":
            self.enqueued = data
            self.emit("clip_generated", "unrelated-clip")
            self.emit("clip_failed" if self.failure == "generation" else "clip_generated")
            return {"type": "clip_queued", "data": {"clip": {"clip_id": "clip-1"}}}
        if command == "play":
            self.emit("clip_finished")

    async def request_recording(self):
        self.calls.append("recording")
        if self.failure == "recording":
            raise RuntimeError("recording unavailable")
        return "recording-ref"

    async def download(self, recording, path, *, ready_timeout):
        assert recording == "recording-ref" and ready_timeout == 10
        self.calls.append("download")
        if self.failure == "download":
            raise TimeoutError("test-secret must not appear")
        if self.failure == "cancel":
            raise asyncio.CancelledError
        Path(path).write_bytes(b"fragmented-mp4-with-audio")

    async def disconnect(self):
        self.calls.append("disconnect")
        if self.failure == "disconnect":
            raise ConnectionError("test-secret must not appear")

    def close(self):
        self.closed = True


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "reactor_api_key", "test-key")
    fake = FakeReactor(r.MODEL, jwt="test-jwt")
    monkeypatch.setattr(r, "_sdk", lambda: lambda *a, **kw: fake)
    monkeypatch.setattr(r.shutil, "which", lambda _: "ffmpeg")
    state = {"rate": "0.0125", "requests": [], "token_status": 200, "delete_status": 204}

    def http(request):
        state["requests"].append(request)
        if request.url.path == "/pricing":
            return httpx.Response(200, json={"models": [{"name": "fast-h3-omni", "rate": {
                "amount_per_sec_usd": state["rate"], "currency_code": "USD", "denomination": "second"}}]})
        if request.url.path == "/tokens":
            assert request.headers["Reactor-API-Key"] == "test-key"
            return httpx.Response(state["token_status"], json={"jwt": "test-jwt"})
        assert request.method == "DELETE" and request.url.path == "/sessions/session-1"
        assert request.headers["authorization"] == "Bearer test-jwt"
        return httpx.Response(state["delete_status"])

    async def remux(*args, **kw):
        assert fake.calls[-1] == "disconnect" and fake.closed  # offline work never holds the paid GPU
        assert [args[i + 1] for i, arg in enumerate(args) if arg == "-map"] == ["0:v:0", "0:a:0"]
        Path(args[-1]).write_bytes(b"standard-mp4-with-audio")

    async def probe(path):
        return SimpleNamespace(has_audio=True, width=1344, duration_ms=5875)

    monkeypatch.setattr(r, "ffmpeg", remux)
    monkeypatch.setattr(r, "probe", probe)
    adapter = r.ReactorAdapter(transport=httpx.MockTransport(http))
    adapter.store.root = tmp_path / "results"
    return adapter, fake, state


async def test_capture_terminates_and_survives_adapter_restart(setup, tmp_path):
    adapter, fake, state = setup
    result = await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="generation-1")
    assert fake.calls == ["connect", "set_canvas", "enqueue", "play", "recording", "download", "disconnect"]
    assert fake.closed
    auth = json.loads(state["requests"][1].content)["authorization_details"][0]
    assert auth["resources"] == {"models": {"match": [r.MODEL]}}
    assert auth["constraints"] == {"max_sessions": 1, "max_session_duration_seconds": 60}
    restarted = r.ReactorAdapter()
    restarted.store.root = adapter.store.root
    status = await restarted.poll(ROUTE, result.job_id)
    assert status.status == "succeeded" and len(status.outputs) == 1
    assert status.usage["cost_estimated"] is True
    assert 0 < status.usage["cost_usd"] <= 0.75
    dest = tmp_path / "ingested.mp4"
    assert await restarted.download(ROUTE, status.outputs[0], dest, tmp_path) == "video/mp4"
    assert dest.read_bytes() == b"standard-mp4-with-audio"
    assert not await restarted.cancel(ROUTE, result.job_id)
    metadata = (adapter.store.root / result.job_id / "meta.json").read_text()
    assert "test-jwt" not in metadata and "test-key" not in metadata


@pytest.mark.parametrize("rate", ["0.02", "NaN", "Infinity", "0", "-1", "not-a-price"])
async def test_price_change_or_invalid_rate_fails_before_paid_session(setup, rate):
    adapter, fake, state = setup
    state["rate"] = rate
    with pytest.raises(ProviderError, match="no session was started") as error:
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert not isinstance(error.value, AmbiguousSubmitError)
    assert not fake.calls
    assert [x.url.path for x in state["requests"]] == ["/pricing"]


async def test_auth_failure_is_safe_to_fail_over(setup):
    adapter, fake, state = setup
    state["token_status"] = 401
    with pytest.raises(ProviderError) as error:
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert not isinstance(error.value, AmbiguousSubmitError) and not fake.calls


@pytest.mark.parametrize("failure", ["connect", "enqueue", "generation", "play", "recording", "download"])
async def test_paid_failures_never_auto_retry_and_always_terminate(setup, failure):
    adapter, fake, _ = setup
    fake.failure = failure
    with pytest.raises(AmbiguousSubmitError, match="no automatic retry") as error:
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert fake.calls[-1] == "disconnect" and fake.closed
    assert "test-secret" not in str(error.value)
    assert fake.calls.count("connect") == 1 and fake.calls.count("enqueue") <= 1
    assert adapter.store.meta(next(adapter.store.root.iterdir()).name)["terminated"] is True


async def test_cancellation_still_releases_session(setup):
    adapter, fake, _ = setup
    fake.failure = "cancel"
    with pytest.raises(asyncio.CancelledError):
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert fake.calls[-1] == "disconnect" and fake.closed


@pytest.mark.parametrize("delete_status", [204, 500])
async def test_disconnect_failure_uses_authenticated_delete(setup, delete_status):
    adapter, fake, state = setup
    fake.failure = "disconnect"
    state["delete_status"] = delete_status
    if delete_status == 204:
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    else:
        with pytest.raises(AmbiguousSubmitError, match="unconfirmed; server cap applies"):
            await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert state["requests"][-1].method == "DELETE" and fake.closed


async def test_staged_files_only_and_sdk_reference_objects_preserved(setup, tmp_path):
    adapter, fake, _ = setup
    path = tmp_path / "input.png"
    path.write_bytes(b"image")
    inputs = await adapter.stage_inputs(ROUTE, [ResolvedMedia("image_references", "a", path, "image/png", "image")])
    await adapter.submit(ROUTE, r.MODEL, {**PAYLOAD, "reference_images": inputs["image_references"],
                                       "first_frame_image": inputs["image_references"]}, client_ref="g")
    assert fake.calls.count("upload") == 1
    assert fake.enqueued["first_frame_image"] is fake.enqueued["reference_images"][0]
    assert not adapter._staged


async def test_arbitrary_path_cannot_be_uploaded(setup, tmp_path):
    adapter, fake, _ = setup
    path = tmp_path / "secret.txt"
    path.write_text("secret")
    with pytest.raises(ProviderError, match="not staged"):
        await adapter.submit(ROUTE, r.MODEL, {**PAYLOAD, "reference_images": [str(path)]}, client_ref="g")
    assert not fake.calls


async def test_missing_recording_is_not_a_retryable_generation_failure(setup):
    adapter, _, _ = setup
    with pytest.raises(ProviderError) as error:
        await adapter.poll(ROUTE, "missing")
    assert error.value.error_class == ErrorClass.route_unavailable


@pytest.mark.parametrize("cap, maximum", [(30, 0.375), (60, 0.75), (120, 1.5)])
def test_registry_reserves_session_lifetime_not_output_duration(cap, maximum):
    req = normalize(SPEC, "t2v", "A bird sings", {"session_seconds": cap, "duration": "14.375"}, [])
    assert float(estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd) == maximum
    endpoint, payload, warnings = build_payload(SPEC, ROUTE, req, {})
    assert endpoint == r.MODEL and payload["seconds"] == 14.375 and payload["session_seconds"] == cap
    assert "duration" not in payload and not warnings


def test_reference_slot_requires_images_for_audio_and_keyframes():
    for mode in ("reference", "i2v", "flf2v"):
        with pytest.raises(ValueError):
            normalize(SPEC, mode, "A bird sings", {}, [MediaRef(role="audio_references", asset_id="audio", kind="audio")])


async def test_incomplete_or_mute_capture_is_not_published(setup, monkeypatch):
    adapter, fake, _ = setup

    async def mute(path):
        return SimpleNamespace(has_audio=False, width=1344, duration_ms=5875)

    monkeypatch.setattr(r, "probe", mute)
    with pytest.raises(AmbiguousSubmitError):
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert fake.closed
    job_id = next(adapter.store.root.iterdir()).name
    with pytest.raises(ProviderError):
        await adapter.poll(ROUTE, job_id)


async def test_session_deadline_cleans_up_without_reenqueue(setup, monkeypatch):
    adapter, fake, _ = setup
    timeout = asyncio.timeout
    monkeypatch.setattr(r.asyncio, "timeout", lambda seconds: timeout(0.01))

    async def stuck(recording, path, *, ready_timeout):
        await asyncio.Event().wait()

    monkeypatch.setattr(fake, "download", stuck)
    with pytest.raises(AmbiguousSubmitError, match="TimeoutError"):
        await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    assert fake.calls.count("enqueue") == 1 and fake.calls[-1] == "disconnect" and fake.closed


def test_key_and_mode_control_the_optional_live_route():
    req = normalize(SPEC, "t2v", "A bird sings", {}, [])
    for mode, keyed, allowlist, eligible in [
        ("live", False, set(), False), ("mock", True, set(), False),
        ("mixed", True, set(), False), ("mixed", True, {ROUTE.id}, True), ("live", True, set(), True),
    ]:
        env = RoutingEnv(provider_mode=mode, live_allowlist=allowlist,
                         has_key=lambda _, keyed=keyed: keyed, has_adapter=lambda _: True)
        candidate = next(c for c in candidates(REG, SPEC, req, env) if c.route.id == ROUTE.id)
        assert candidate.eligible is eligible


async def test_balance_card_has_dashboard_without_inventing_balance(monkeypatch):
    from studio.budget import balances

    for _, _, key in balances.PROVIDERS.values():
        monkeypatch.setattr(get_settings(), key, "test-key" if key == "reactor_api_key" else "")
    balances.clear_cache()
    try:
        cards = await balances.balances()
        assert len(cards) == 1 and cards[0].provider == "reactor"
        assert cards[0].status == "no_api" and cards[0].amount is None
    finally:
        balances.clear_cache()


async def test_actual_mp4_remux_preserves_picture_and_sound(setup, monkeypatch, tmp_path):
    from studio.media.ffmpeg import ffmpeg, probe

    adapter, fake, _ = setup
    source = tmp_path / "fragmented.mp4"
    # Local synthetic capture, not model inference. Exercise the actual ffmpeg/ffprobe/ingest path.
    await ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24", "-f", "lavfi", "-i",
                 "sine=frequency=440:sample_rate=48000", "-t", "5.875", "-c:v", "libx264",
                 "-c:a", "aac", "-movflags", "+frag_keyframe+empty_moov", str(source), timeout=20)

    async def copy_capture(recording, path, *, ready_timeout):
        assert "play" in fake.calls
        r.shutil.copyfile(source, path)

    monkeypatch.setattr(fake, "download", copy_capture)
    monkeypatch.setattr(r, "ffmpeg", ffmpeg)
    monkeypatch.setattr(r, "probe", probe)
    result = await adapter.submit(ROUTE, r.MODEL, PAYLOAD, client_ref="g")
    status = await adapter.poll(ROUTE, result.job_id)
    dest = tmp_path / "library.mp4"
    await adapter.download(ROUTE, status.outputs[0], dest, tmp_path)
    info = await probe(dest)
    assert info.width == 64 and info.has_audio and info.duration_ms >= 5625
    assert fake.closed and fake.calls[-1] == "disconnect"
