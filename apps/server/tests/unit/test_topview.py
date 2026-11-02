"""Offline Topview contracts: supported scope, one submit, settled billing and recovery."""

import json
from decimal import Decimal

import httpx
import pytest

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import AmbiguousSubmitError, ProviderError, ResolvedMedia
from studio.providers.topview import TopviewAdapter
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.validate import normalize

REG = load_registry(get_settings().resolve_registry_dir())
SPEC = REG.model("topview-t1")
ROUTE = SPEC.route("topview-t1@topview")
ENDPOINT = "models/topview/topview-t1/text-to-video/predictions"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(get_settings(), "topview_api_key", "offline-test")


@pytest.mark.parametrize("duration", [4, 5, 30])
@pytest.mark.parametrize("audio", [True, False])
@pytest.mark.parametrize("ratio", SPEC.aspect_ratios)
def test_payload_and_price(duration, audio, ratio):
    req = normalize(SPEC, "t2v", "A paper boat", {
        "duration": duration, "generate_audio": audio, "aspect_ratio": ratio}, [])
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {})
    assert endpoint == ENDPOINT
    assert body == {"content": [{"type": "text", "text": "A paper boat"}],
                    "duration": duration, "resolution": "720p", "ratio": ratio, "generate_audio": audio}
    assert estimate(ROUTE.pricing, req.params, req.prompt, [], req.mode).usd == Decimal("0.1649") * duration


@pytest.mark.parametrize("mode,params", [("i2v", {}), ("omni_reference", {}),
                                        ("t2v", {"resolution": "1080p"}), ("t2v", {"duration": -1}),
                                        ("t2v", {"duration": 31}), ("t2v", {"duration": 3})])
def test_unsupported_scope_rejected(mode, params):
    with pytest.raises(ValueError):
        normalize(SPEC, mode, "A boat", params, [])


@pytest.mark.asyncio
async def test_no_media_upload(tmp_path):
    adapter = TopviewAdapter()
    assert await adapter.stage_inputs(ROUTE, []) == {}
    with pytest.raises(ProviderError):
        await adapter.stage_inputs(ROUTE, [ResolvedMedia("start_image", "a", tmp_path / "a.png", "image/png", "image")])
    assert await adapter.cancel(ROUTE, "task-1") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["accepted", "timeout", "malformed", "missing", "500", "502", "409", "401"])
async def test_submission_is_single_and_idempotent(outcome):
    calls = []
    body = {"content": [{"type": "text", "text": "A boat"}], "duration": 5, "resolution": "720p"}

    def http(req):
        calls.append(req)
        assert req.method == "POST"
        assert str(req.url) == f"https://platform-backend.topview.ai/api/v1/{ENDPOINT}"
        assert req.headers["Authorization"] == "Bearer offline-test"
        assert req.headers["Idempotency-Key"] == "attempt-1"
        assert json.loads(req.content) == body
        if outcome == "timeout":
            raise httpx.ReadTimeout("response lost", request=req)
        if outcome == "malformed":
            return httpx.Response(200, text="not-json")
        if outcome == "missing":
            return httpx.Response(200, json={"data": {"id": ""}})
        if outcome.isdigit():
            return httpx.Response(int(outcome), json={"message": "error"})
        return httpx.Response(200, json={"code": 200, "data": {"id": "task-1", "status": "created"}})

    adapter = TopviewAdapter(httpx.MockTransport(http))
    if outcome == "accepted":
        assert (await adapter.submit(ROUTE, ENDPOINT, body, client_ref="attempt-1")).job_id == "task-1"
    else:
        with pytest.raises(ProviderError if outcome == "401" else AmbiguousSubmitError):
            await adapter.submit(ROUTE, ENDPOINT, body, client_ref="attempt-1")
    assert len(calls) == 1


def task(**changes):
    return {"id": "task-1", "status": "completed", "billing_state": "CAPTURED", "charged_usd": "0.8245",
            "outputs": ["https://cdn.example.test/video.mp4"], **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("created", "queued"), ("processing", "running"),
                                            ("completed", "succeeded"), ("failed", "failed")])
async def test_poll_maps_status_and_final_charges(status, expected):
    data = task(status=status)
    if status == "failed":
        data.update(billing_state="RELEASED", charged_usd="0.000000000000", outputs=[])

    def http(req):
        assert req.method == "GET" and req.url.path == "/api/v1/predictions/task-1"
        return httpx.Response(200, json={"code": 200, "data": data})

    res = await TopviewAdapter(httpx.MockTransport(http)).poll(ROUTE, "task-1")
    assert res.status == expected
    if status in ("completed", "failed"):
        assert res.usage == {"cost_usd": float(data["charged_usd"]), "cost_estimated": False}
    else:
        assert res.usage == {}
    if status == "completed":
        assert res.outputs[0].url == data["outputs"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [task(status="payment_required"), task(status="unknown"),
    task(billing_state="RESERVED"), task(charged_usd=None), task(charged_usd="NaN"),
    task(charged_usd="-1"), task(charged_usd=True), task(charged_usd="Infinity"), task(outputs=[]),
    task(outputs=["http://cdn.example.test/v.mp4"]), task(id="different-task"),
    task(status="failed", billing_state="RELEASED", charged_usd="0.5"), None])
async def test_uncertain_result_parks_original_job(data):
    adapter = TopviewAdapter(httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data})))
    with pytest.raises(ProviderError) as exc:
        await adapter.poll(ROUTE, "task-1")
    # The worker parks non-transient status errors as unknown, retaining the accepted ID.
    assert exc.value.error_class == ErrorClass.route_unavailable


@pytest.mark.asyncio
@pytest.mark.parametrize("code,error", [(401, ErrorClass.route_unavailable), (404, ErrorClass.route_unavailable),
                                      (429, ErrorClass.transient), (503, ErrorClass.transient)])
async def test_status_errors_never_submit(code, error):
    calls = []

    def http(req):
        calls.append(req.method)
        return httpx.Response(code, json={"message": "status error"})

    with pytest.raises(ProviderError) as exc:
        await TopviewAdapter(httpx.MockTransport(http)).poll(ROUTE, "task-1")
    assert exc.value.error_class == error
    assert calls == ["GET"]


def test_explicit_refund_is_authoritative():
    from studio.worker.tasks.generation import _reported_cost

    assert _reported_cost({"cost_usd": 0, "cost_estimated": False}) == Decimal("0")
    assert _reported_cost({"cost_usd": 0}) is None
    assert _reported_cost({"cost_usd": 0, "cost_estimated": True}) is None


def test_provider_wiring_and_mock_route():
    from studio.keys import KEYS
    from studio.providers.factory import get_adapter

    assert REG.providers["topview"].key_setting in KEYS
    assert isinstance(get_adapter("topview"), TopviewAdapter)
    assert any(r.provider == "mock" and "t2v" in r.modes for r in SPEC.routes)


def test_mock_mode_never_selects_paid_route_even_with_a_key():
    from studio.registry.routing import RoutingEnv, candidates

    req = normalize(SPEC, "t2v", "A boat", {}, [])
    env = RoutingEnv(provider_mode="mock", live_allowlist=set(),
                     has_key=lambda _: True, has_adapter=lambda _: True)
    eligible = [c for c in candidates(REG, SPEC, req, env) if c.eligible]
    assert eligible and all(c.route.provider == "mock" for c in eligible)
    assert all(c.estimate.usd == 0 for c in eligible)


@pytest.mark.asyncio
async def test_output_ingested_to_disk_without_platform_credentials(monkeypatch, tmp_path):
    from studio.providers import common
    from studio.providers.base import ProviderOutput

    original_client = httpx.AsyncClient

    def http(req):
        assert req.method == "GET"
        assert req.url.host == "cdn.example.test"
        assert "Authorization" not in req.headers
        return httpx.Response(200, content=b"offline-video", headers={"Content-Type": "video/mp4"})

    def client(**kwargs):
        return original_client(**kwargs, transport=httpx.MockTransport(http))

    monkeypatch.setattr(common.httpx, "AsyncClient", client)
    dest = tmp_path / "output.mp4"
    output = ProviderOutput(index=0, kind="video", url="https://cdn.example.test/video.mp4")
    assert await TopviewAdapter().download(ROUTE, output, dest, tmp_path) == "video/mp4"
    assert dest.read_bytes() == b"offline-video"
