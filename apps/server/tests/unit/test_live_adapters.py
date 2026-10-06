"""Live provider adapters exercised without keys or network: HTTP mocked with respx, SDKs with fakes.

These pin each adapter's mechanics (submit/poll/cancel/download, status mapping, error classes). Field
names inside payloads are registry data and are covered by the contract tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import fal_client
import httpx
import pytest
import respx
from google.genai import types

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import AmbiguousSubmitError, ProviderError, ProviderOutput
from studio.providers.common import classify_http, extract_outputs
from studio.providers.fal import FalAdapter, app_base
from studio.providers.google_genai import GoogleGenAIAdapter
from studio.providers.replicate import ReplicateAdapter
from studio.providers.runway import RunwayAdapter
from studio.registry.schema import OutputSpec, Pricing, Rate, Route, RouteMode


def route(kind="video", path="output", endpoint="x") -> Route:
    return Route(id="m@p", provider="p", modes={"t2v": RouteMode(endpoint=endpoint)},
                 pricing=Pricing(basis="per_request", rates=[Rate(usd=0)]), output=OutputSpec(path=path, kind=kind))


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    for k in ("fal_key", "gemini_api_key", "replicate_api_token", "runway_api_key"):
        monkeypatch.setattr(s, k, "test-key")


# ---------------------------------------------------------------- shared helpers


def test_classify_http():
    assert classify_http(401) == ErrorClass.route_unavailable
    assert classify_http(402) == ErrorClass.route_unavailable
    assert classify_http(422) == ErrorClass.invalid_request
    assert classify_http(429) == ErrorClass.transient
    assert classify_http(503) == ErrorClass.transient
    assert classify_http(400, "Prompt rejected by content policy") == ErrorClass.moderation


def test_extract_outputs_shapes():
    assert [o.url for o in extract_outputs({"video": {"url": "https://a/v.mp4"}}, "video.url", "video")] == ["https://a/v.mp4"]
    imgs = extract_outputs({"images": [{"url": "https://a/1.png", "content_type": "image/png"}, {"url": "https://a/2.png"}]},
                           "images", "image")
    assert [o.index for o in imgs] == [0, 1] and imgs[0].mime == "image/png"
    assert [o.url for o in extract_outputs({"output": ["https://r/x.mp4"]}, "output", "video")] == ["https://r/x.mp4"]
    assert extract_outputs({"output": None}, "output", "video") == []


def test_fal_app_base():
    assert app_base("fal-ai/veo3.1/image-to-video") == "fal-ai/veo3.1"
    assert app_base("fal-ai/kling-video/v3/pro/text-to-video") == "fal-ai/kling-video"
    assert app_base("bytedance/seedance-2.5/text-to-video") == "bytedance/seedance-2.5"


# ---------------------------------------------------------------- Replicate (respx)

R = "https://api.replicate.com/v1"


@respx.mock
async def test_replicate_lifecycle(keys, tmp_path):
    submit = respx.post(f"{R}/models/bytedance/seedance-2.5/predictions").respond(
        201, json={"id": "pred1", "status": "starting"})
    status = respx.get(f"{R}/predictions/pred1")
    status.side_effect = [
        httpx.Response(200, json={"id": "pred1", "status": "starting"}),
        httpx.Response(200, json={"id": "pred1", "status": "processing"}),
        httpx.Response(200, json={"id": "pred1", "status": "succeeded", "output": "https://replicate.delivery/x/out.mp4",
                                  "metrics": {"predict_time": 41.2}}),
    ]
    respx.get("https://replicate.delivery/x/out.mp4").respond(200, content=b"mp4", headers={"content-type": "video/mp4"})
    a, rt = ReplicateAdapter(), route()
    res = await a.submit(rt, "bytedance/seedance-2.5", {"prompt": "x", "duration": 4}, client_ref="c")
    assert json.loads(submit.calls.last.request.content) == {"input": {"prompt": "x", "duration": 4}}
    assert submit.calls.last.request.headers["authorization"] == "Bearer test-key"
    assert (await a.poll(rt, res.job_id)).status == "queued"
    assert (await a.poll(rt, res.job_id)).status == "running"
    done = await a.poll(rt, res.job_id)
    assert done.status == "succeeded" and done.usage["predict_time"] == 41.2
    dest = tmp_path / "o.mp4"
    assert await a.download(rt, done.outputs[0], dest, tmp_path) == "video/mp4"
    assert dest.read_bytes() == b"mp4"


@respx.mock
async def test_replicate_errors(keys):
    a, rt = ReplicateAdapter(), route()
    respx.post(f"{R}/models/o/m/predictions").respond(401, json={"detail": "Invalid token"})
    with pytest.raises(ProviderError) as exc:
        await a.submit(rt, "o/m", {}, client_ref="c")
    assert exc.value.error_class == ErrorClass.route_unavailable and "check the API key" in exc.value.message

    respx.post(f"{R}/predictions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousSubmitError):
        await a.submit(rt, "o/m:abc123", {}, client_ref="c")

    respx.get(f"{R}/predictions/bad").respond(200, json={"status": "failed", "error": "NSFW content detected"})
    failed = await a.poll(rt, "bad")
    assert failed.status == "failed" and failed.error_class == ErrorClass.moderation


async def test_replicate_without_key_is_route_unavailable(monkeypatch):
    monkeypatch.setattr(get_settings(), "replicate_api_token", "")
    with pytest.raises(ProviderError) as exc:
        await ReplicateAdapter().submit(route(), "o/m", {}, client_ref="c")
    assert exc.value.error_class == ErrorClass.route_unavailable


# ---------------------------------------------------------------- Runway (respx)

RW = "https://api.dev.runwayml.com/v1"


@respx.mock
async def test_runway_lifecycle_and_headers(keys, tmp_path):
    submit = respx.post(f"{RW}/image_to_video").respond(200, json={"id": "task1"})
    status = respx.get(f"{RW}/tasks/task1")
    status.side_effect = [
        httpx.Response(200, json={"id": "task1", "status": "THROTTLED"}),
        httpx.Response(200, json={"id": "task1", "status": "RUNNING", "progress": 0.4}),
        httpx.Response(200, json={"id": "task1", "status": "SUCCEEDED", "output": ["https://dnznrvs05pmza.cloudfront.net/v.mp4"]}),
    ]
    a, rt = RunwayAdapter(), route()
    res = await a.submit(rt, "image_to_video", {"model": "gen4_turbo", "promptText": "x", "promptImage": "data:..."},
                         client_ref="c")
    req = submit.calls.last.request
    assert req.headers["x-runway-version"] == "2024-11-06" and req.headers["authorization"] == "Bearer test-key"
    assert (await a.poll(rt, res.job_id)).status == "queued"
    running = await a.poll(rt, res.job_id)
    assert running.status == "running" and running.progress == pytest.approx(0.4)
    assert (await a.poll(rt, res.job_id)).outputs[0].url.endswith("v.mp4")


@respx.mock
async def test_runway_safety_failure_and_cancel(keys):
    respx.get(f"{RW}/tasks/t2").respond(200, json={"status": "FAILED", "failure": "Input flagged", "failureCode": "SAFETY.INPUT.IMAGE"})
    failed = await RunwayAdapter().poll(route(), "t2")
    assert failed.error_class == ErrorClass.moderation
    respx.delete(f"{RW}/tasks/t3").respond(204)
    assert await RunwayAdapter().cancel(route(), "t3")


@respx.mock
async def test_runway_inlines_small_images_and_uploads_large_ones(keys, tmp_path, monkeypatch):
    from studio.providers import runway as runway_mod
    from studio.providers.base import ResolvedMedia

    async def no_cache(provider, media, upload):  # the DB-backed cache isn't under test here
        return (await upload(media))[0]

    monkeypatch.setattr(runway_mod, "staged_ref", no_cache)
    small = tmp_path / "s.png"
    small.write_bytes(bytes([0x89]) + b"PNG" + b"0" * 100)
    staged = await RunwayAdapter().stage_inputs(route(), [ResolvedMedia("start_image", "a", small, "image/png", "image")])
    assert staged["start_image"][0].startswith("data:image/png;base64,")

    init = respx.post(f"{RW}/uploads").respond(200, json={
        "uploadUrl": "https://upload.runway.test/bucket", "fields": {"key": "k1", "policy": "p"},
        "runwayUri": "runway://uploads/k1"})
    put = respx.post("https://upload.runway.test/bucket").respond(204)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 1024)
    staged = await RunwayAdapter().stage_inputs(route(), [ResolvedMedia("input_video", "v", clip, "video/mp4", "video")])
    assert staged["input_video"] == ["runway://uploads/k1"]
    assert json.loads(init.calls.last.request.content) == {"filename": "clip.mp4", "type": "ephemeral"}
    assert b'name="key"' in put.calls.last.request.content  # presigned form fields are sent with the file


def test_runway_failure_codes():
    from studio.providers.runway import classify_failure

    assert classify_failure("SAFETY.INPUT.IMAGE") == ErrorClass.moderation
    assert classify_failure("INPUT_PREPROCESSING.SAFETY.TEXT") == ErrorClass.moderation
    assert classify_failure("ASSET.INVALID") == ErrorClass.invalid_request
    assert classify_failure("INTERNAL.BAD_OUTPUT.01") == ErrorClass.transient
    assert classify_failure("THIRD_PARTY.UNAVAILABLE") == ErrorClass.transient


# ---------------------------------------------------------------- fal (fake SDK client)


class FakeFal:
    def __init__(self, statuses, result=None, submit_error=None):
        self.statuses = list(statuses)
        self.result_value = result
        self.submit_error = submit_error
        self.calls = []

    async def submit(self, endpoint, arguments):
        self.calls.append(("submit", endpoint, arguments))
        if self.submit_error:
            raise self.submit_error
        return SimpleNamespace(request_id="req-1")

    async def status(self, app, request_id, with_logs=False):
        self.calls.append(("status", app, request_id))
        return self.statuses.pop(0)

    async def result(self, app, request_id):
        return self.result_value

    async def cancel(self, app, request_id):
        self.calls.append(("cancel", app, request_id))


async def test_fal_lifecycle():
    fake = FakeFal([fal_client.Queued(position=0), fal_client.InProgress(logs=[]),
                    fal_client.Completed(logs=[], metrics={"inference_time": 30.5})],
                   result={"video": {"url": "https://v3.fal.media/files/v.mp4", "content_type": "video/mp4"}})
    a, rt = FalAdapter(client=fake), route(path="video.url")
    res = await a.submit(rt, "fal-ai/veo3.1/image-to-video", {"prompt": "x"}, client_ref="c")
    assert res.job_id == "fal-ai/veo3.1::req-1"
    q = await a.poll(rt, res.job_id)
    assert q.status == "queued" and q.queue_position == 1
    assert fake.calls[-1] == ("status", "fal-ai/veo3.1", "req-1")
    assert (await a.poll(rt, res.job_id)).status == "running"
    done = await a.poll(rt, res.job_id)
    assert done.status == "succeeded" and done.outputs[0].mime == "video/mp4"
    assert done.usage == {"inference_time": 30.5}
    assert await a.cancel(rt, res.job_id) and fake.calls[-1][0] == "cancel"


async def test_fal_errors():
    http_err = fal_client.FalClientHTTPError(message="Unauthorized", status_code=401, response_headers={},
                                             response=httpx.Response(401))
    with pytest.raises(ProviderError) as exc:
        await FalAdapter(client=FakeFal([], submit_error=http_err)).submit(route(), "fal-ai/x", {}, client_ref="c")
    assert exc.value.error_class == ErrorClass.route_unavailable
    with pytest.raises(AmbiguousSubmitError):
        await FalAdapter(client=FakeFal([], submit_error=httpx.ReadTimeout("t"))).submit(route(), "fal-ai/x", {}, client_ref="c")
    blocked = FakeFal([fal_client.Completed(logs=[], metrics={}, error="Content policy violation", error_type="content_policy_violation")])
    failed = await FalAdapter(client=blocked).poll(route(), "fal-ai/x::r")
    assert failed.status == "failed" and failed.error_class == ErrorClass.moderation


# ---------------------------------------------------------------- Google (fake SDK client)


class FakeGenAI:
    def __init__(self, ops=None, image_bytes=b"\x89PNGimg", block=None):
        self.ops = list(ops or [])
        self.generated = []
        self.image_bytes = image_bytes
        self.block = block
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_videos=self._gen_videos, generate_content=self._gen_content),
                                   operations=SimpleNamespace(get=self._get), files=SimpleNamespace(download=self._download))

    async def _gen_videos(self, model, prompt, image, config):
        self.generated.append({"model": model, "prompt": prompt, "image": image, "config": config})
        return SimpleNamespace(name="models/veo-3.1/operations/op1")

    async def _get(self, op):
        return self.ops.pop(0)

    async def _download(self, file):
        return b"veo-mp4"

    async def _gen_content(self, model, contents, config):
        self.generated.append({"model": model, "contents": contents, "config": config})
        if self.block:
            cand = SimpleNamespace(content=None, finish_reason=self.block)
            return SimpleNamespace(candidates=[cand], prompt_feedback=None)
        part = SimpleNamespace(inline_data=SimpleNamespace(data=self.image_bytes, mime_type="image/png"))
        return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason="STOP")])


def op(done, videos=None, error=None, filtered=None):
    resp = SimpleNamespace(generated_videos=videos or [], rai_media_filtered_count=len(filtered or []),
                           rai_media_filtered_reasons=filtered or []) if done else None
    return SimpleNamespace(done=done, error=error, response=resp, result=resp)


async def test_veo_lifecycle_with_frames(tmp_path):
    img = tmp_path / "f.png"
    img.write_bytes(b"\x89PNG")
    video = SimpleNamespace(video=SimpleNamespace(uri="https://generativelanguage.googleapis.com/v1beta/files/v1:download",
                                                  mime_type="video/mp4"))
    fake = FakeGenAI(ops=[op(False), op(True, videos=[video])])
    a, rt = GoogleGenAIAdapter(client=fake), route()
    ref = {"inline": str(img), "mime": "image/png"}
    res = await a.submit(rt, "veo-3.1-generate-preview", {
        "prompt": "x", "duration_seconds": 8, "aspect_ratio": "16:9", "resolution": "720p", "seed": 3,
        "image": ref, "last_frame": ref, "reference_images": [ref]}, client_ref="c")
    call = fake.generated[0]
    assert call["image"].image_bytes == b"\x89PNG"
    assert call["config"].duration_seconds == 8 and call["config"].last_frame is not None
    assert call["config"].reference_images[0].reference_type == types.VideoGenerationReferenceType.ASSET
    assert (await a.poll(rt, res.job_id)).status == "running"
    done = await a.poll(rt, res.job_id)
    assert done.status == "succeeded"
    dest = tmp_path / "v.mp4"
    assert await a.download(rt, done.outputs[0], dest, tmp_path) == "video/mp4"
    assert dest.read_bytes() == b"veo-mp4"


async def test_veo_filtered_is_moderation():
    fake = FakeGenAI(ops=[op(True, filtered=["Your request may contain a minor"])])
    res = await GoogleGenAIAdapter(client=fake).poll(route(), "models/veo/operations/x")
    assert res.status == "failed" and res.error_class == ErrorClass.moderation


async def test_nano_banana_is_synchronous_and_multi_image(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "media_root", tmp_path / "media")
    fake = FakeGenAI()
    a, rt = GoogleGenAIAdapter(client=fake), route(kind="image", path="images")
    a.store.root = tmp_path / "results"
    res = await a.submit(rt, "gemini-3.1-flash-image", {"prompt": "ramen", "num_images": 2, "resolution": "2k",
                                                        "aspect_ratio": "16:9"}, client_ref="c")
    assert len(fake.generated) == 2 and fake.generated[0]["config"].image_config.image_size == "2K"
    done = await a.poll(rt, res.job_id)
    assert done.status == "succeeded" and len(done.outputs) == 2
    dest = tmp_path / "i.png"
    await a.download(rt, done.outputs[1], dest, tmp_path)
    assert dest.read_bytes() == b"\x89PNGimg"


async def test_nano_banana_block_is_moderation(tmp_path):
    a, rt = GoogleGenAIAdapter(client=FakeGenAI(block="IMAGE_SAFETY")), route(kind="image", path="images")
    a.store.root = tmp_path / "results"
    res = await a.submit(rt, "gemini-3.1-flash-image", {"prompt": "x"}, client_ref="c")
    failed = await a.poll(rt, res.job_id)
    assert failed.status == "failed" and failed.error_class == ErrorClass.moderation


def test_output_ref_shape_for_veo():
    out = ProviderOutput(index=0, kind="video", ref="https://generativelanguage.googleapis.com/x")
    assert out.ref.startswith("http")
