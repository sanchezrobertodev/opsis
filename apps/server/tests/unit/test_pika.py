"""Offline Pika transport tests: spending checks, settled charges, and safe media persistence."""

import json

import httpx
import pytest

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers import pika
from studio.providers.base import AmbiguousSubmitError, ProviderError, ProviderOutput, ResolvedMedia
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.validate import MediaRef, NormalizedRequest

SPEC = load_registry(get_settings().resolve_registry_dir()).model("seedance-2.5")
ROUTE = next(r for r in SPEC.routes if r.provider == "pika")
ENDPOINT = ROUTE.modes["t2v"].endpoint
BALANCE = {"unit": "micro_usd", "currency": "usd", "balance_micro_usd": 10_000_000}
PAYLOAD = {"prompt": "A cat", "duration": 5, "_studio_max_cost_usd": "1.4"}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(get_settings(), "pika_api_key", "test-key")


def test_spending_capacity():
    assert pika.spending_capacity(BALANCE) == (10_000_000, False)
    assert pika.spending_capacity({**BALANCE, "postpaid": {
        "active": True, "cycle": {"remaining_micro_usd": 123}}}) == (123, True)
    for body in ({}, {**BALANCE, "unit": "usd"}, {**BALANCE, "balance_micro_usd": True}):
        with pytest.raises(ValueError):
            pika.spending_capacity(body)


@pytest.mark.parametrize("mode", list(ROUTE.modes))
def test_payload_contract_and_reservation(mode):
    media = [MediaRef("input_video", "asset", "video", duration_ms=5000)] if mode in ("video_edit", "extend") else []
    params = {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9", "seed": 123, "generate_audio": True}
    if mode.startswith("multi_shot"):
        params["shots"] = [{"prompt": "First shot", "duration": 5}]
    req = NormalizedRequest(SPEC.id, mode, "A cat", "noise", params, media)
    inputs = {"input_video": ["https://media.test/v.mp4"]} if media else {}
    if mode in ("i2v", "multi_shot_frame"):
        inputs = {"start_image": ["https://media.test/start.png"], "end_image": ["https://media.test/end.png"]}
    if mode == "omni_reference":
        inputs = {"image_references": ["https://media.test/ref.png"]}
    _, body, _ = build_payload(SPEC, ROUTE, req, inputs)
    assert "seed" not in body and "aspect_ratio" not in body and "negative_prompt" not in body
    # Pika's live quotes on 2026-10-30 (a 5 s clip is held as 30 s; an edit's output as 30 s more)
    quoted = {"video_edit": 9.580032, "extend": 5.588359}.get(mode, 1.334729)
    assert float(body["_studio_max_cost_usd"]) >= quoted
    if media:
        assert body["video_urls"] == inputs["input_video"]
        assert body["omni_reference_task_type"] == ("edit" if mode == "video_edit" else "extend")
        assert body["duration"] == ("auto" if mode == "video_edit" else 5)
    if mode in ("i2v", "multi_shot_frame"):
        assert body["image_url"] == inputs["start_image"][0] and body["ratio"] == "adaptive"
    if mode == "omni_reference":
        assert body["image_urls"] == inputs["image_references"]
        assert body["omni_reference_task_type"] == "reference"
    if mode.startswith("multi_shot"):
        assert "First shot" in body["prompt"] and "shots" not in body


@pytest.mark.parametrize("durations", [[None], [1000], [31000], [20000, 20000]])
def test_invalid_reference_duration_blocks_preparation(durations):
    req = NormalizedRequest(SPEC.id, "omni_reference", "A cat", None,
                            {"duration": 5, "resolution": "720p"},
                            [MediaRef("video_references", str(i), "video", duration_ms=d) for i, d in enumerate(durations)])
    with pytest.raises(ValueError):
        build_payload(SPEC, ROUTE, req, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["queued", "rejected", "timeout", "missing"])
async def test_submit_once(outcome):
    calls = []

    def http(req):
        calls.append(req)
        assert req.headers["X-API-Key"] == "test-key"
        if req.url.path.endswith("/quote"):
            return httpx.Response(200, json={"micro_usd": 1_334_729})
        if req.url.path == "/billing/balance":
            return httpx.Response(200, json=BALANCE)
        assert req.headers["Idempotency-Key"] == "attempt-1"
        assert json.loads(req.content) == {"prompt": "A cat", "duration": 5}
        if outcome == "timeout":
            raise httpx.ReadTimeout("lost response", request=req)
        if outcome == "missing":
            return httpx.Response(200, json={})
        return httpx.Response(403 if outcome == "rejected" else 200,
                              json={"id": "media_1", "status": "failed" if outcome == "rejected" else "queued"})

    adapter = pika.PikaAdapter(httpx.MockTransport(http))
    if outcome in ("timeout", "missing"):
        with pytest.raises(AmbiguousSubmitError):
            await adapter.submit(ROUTE, ENDPOINT, PAYLOAD, client_ref="attempt-1")
    else:
        assert (await adapter.submit(ROUTE, ENDPOINT, PAYLOAD, client_ref="attempt-1")).job_id == "media_1"
    assert len(calls) == 3
    assert "_studio_max_cost_usd" in PAYLOAD


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["price", "balance", "malformed"])
async def test_preflight_never_generates_on_failure(reason):
    def http(req):
        if req.url.path.endswith("/quote"):
            return httpx.Response(200, json={"micro_usd": 2_000_000 if reason == "price" else 1_000_000})
        assert req.url.path == "/billing/balance"
        return httpx.Response(200, json={} if reason == "malformed" else {**BALANCE, "balance_micro_usd": 0})

    with pytest.raises(ProviderError) as exc:
        await pika.PikaAdapter(httpx.MockTransport(http)).submit(ROUTE, ENDPOINT, PAYLOAD, client_ref="a")
    assert exc.value.error_class == ErrorClass.route_unavailable


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed"])
@pytest.mark.parametrize("charge", [0, 1_213_380])
async def test_poll_status_and_actual_charge(status, charge):
    body = {"status": status, "billing": {"state": "settled", "charge_micro_usd": charge},
            "output": {"video": {"url": "https://cdn.test/a.mp4", "content_type": "video/mp4"}},
            "error": {"code": "content_moderation"}}
    result = await pika.PikaAdapter(httpx.MockTransport(lambda _: httpx.Response(200, json=body))).poll(ROUTE, "media_1")
    assert result.status == ("succeeded" if status == "completed" else status)
    if status in ("completed", "failed"):
        assert result.usage == {"cost_usd": charge / 1_000_000, "cost_estimated": False}
    if status == "completed":
        assert result.outputs[0].url == "https://cdn.test/a.mp4"
    if status == "failed":
        assert result.error_class == ErrorClass.moderation


@pytest.mark.asyncio
@pytest.mark.parametrize("billing", [{"state": "pending"}, {"state": "unavailable"},
                                      {"state": "settled", "charge_micro_usd": None}])
async def test_unsettled_cost_parks_job(monkeypatch, billing):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(pika.asyncio, "sleep", sleep)
    adapter = pika.PikaAdapter(httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": "completed", "billing": billing})))
    with pytest.raises(ProviderError) as exc:
        await adapter.poll(ROUTE, "media_1")
    assert exc.value.error_class == ErrorClass.route_unavailable
    if billing["state"] == "unavailable":
        assert waits == [30]


@pytest.mark.asyncio
async def test_upload_signed_headers_and_bytes(monkeypatch, tmp_path):
    path = tmp_path / "input.png"
    path.write_bytes(b"image-bytes")

    async def uncached(provider, media, uploader):
        return (await uploader(media))[0]

    monkeypatch.setattr(pika, "staged_ref", uncached)

    async def http(req):
        if req.url.host == "api.dev.pika.art":
            assert json.loads(req.content) == {"content_type": "image/png", "size_bytes": 11}
            return httpx.Response(200, json={"upload_url": "https://storage.test/upload",
                "headers": {"Content-Type": "image/png", "Content-Length": "11"},
                "url": "https://storage.test/input.png"})
        assert "x-api-key" not in req.headers
        assert req.headers["Content-Length"] == "11"
        assert await req.aread() == b"image-bytes"
        return httpx.Response(204)

    result = await pika.PikaAdapter(httpx.MockTransport(http)).stage_inputs(ROUTE, [
        ResolvedMedia("image", "asset", path, "image/png", "image")])
    assert result == {"image": ["https://storage.test/input.png"]}


@pytest.mark.asyncio
async def test_download_redirect_does_not_leak_key_and_cancel_does_not_delete(tmp_path):
    def http(req):
        assert req.method == "GET"
        if req.url.host == "api.dev.pika.art":
            assert req.headers["X-API-Key"] == "test-key"
            return httpx.Response(302, headers={"Location": "https://cdn.test/video.mp4"})
        assert "x-api-key" not in req.headers
        return httpx.Response(200, content=b"saved-video")

    adapter = pika.PikaAdapter(httpx.MockTransport(http))
    dest = tmp_path / "video.mp4"
    assert await adapter.download(ROUTE, ProviderOutput(0, "video", url=pika.API + "/v1/files/video"),
                                  dest, tmp_path) == "video/mp4"
    assert dest.read_bytes() == b"saved-video"
    assert await adapter.cancel(ROUTE, "media_1") is False
