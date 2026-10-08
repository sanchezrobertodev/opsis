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


# ---------------------------------------------------------------- RunPod

RP = "https://api.runpod.ai/v2"


@pytest.fixture
def runpod_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "runpod_api_key", "rp-key")


@respx.mock
async def test_runpod_lifecycle_reports_cost(runpod_key):
    from studio.providers.runpod import RunPodAdapter

    submit = respx.post(f"{RP}/wan-2-2-i2v-720/run").respond(200, json={"id": "job-1", "status": "IN_QUEUE"})
    status = respx.get(f"{RP}/wan-2-2-i2v-720/status/job-1")
    status.side_effect = [
        httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"}),
        httpx.Response(200, json={"id": "job-1", "status": "IN_PROGRESS"}),
        httpx.Response(200, json={"id": "job-1", "status": "COMPLETED", "executionTime": 90000,
                                  "output": {"video_url": "https://video.runpod.ai/x/output.mp4", "cost": 0.3}}),
    ]
    a, rt = RunPodAdapter(), route(path="output.video_url")
    res = await a.submit(rt, "wan-2-2-i2v-720", {"prompt": "x", "image": "https://f/i.png"}, client_ref="c")
    assert res.job_id == "wan-2-2-i2v-720::job-1"
    req = submit.calls.last.request
    assert req.headers["authorization"] == "Bearer rp-key"
    assert json.loads(req.content) == {"input": {"prompt": "x", "image": "https://f/i.png"}}
    assert (await a.poll(rt, res.job_id)).status == "queued"
    assert (await a.poll(rt, res.job_id)).status == "running"
    done = await a.poll(rt, res.job_id)
    assert done.status == "succeeded" and done.outputs[0].url.endswith("output.mp4")
    assert done.usage == {"cost_usd": 0.3}


@respx.mock
async def test_runpod_failures_and_cancel(runpod_key):
    from studio.providers.runpod import RunPodAdapter

    a = RunPodAdapter()
    respx.get(f"{RP}/e/status/j1").respond(200, json={"status": "FAILED", "error": "NSFW content detected"})
    assert (await a.poll(route(), "e::j1")).error_class == ErrorClass.moderation
    respx.get(f"{RP}/e/status/j2").respond(200, json={"status": "TIMED_OUT"})
    assert (await a.poll(route(), "e::j2")).error_class == ErrorClass.timeout
    respx.get(f"{RP}/e/status/j3").respond(404)
    gone = await a.poll(route(), "e::j3")
    assert gone.status == "failed" and "30 minutes" in gone.error_message
    respx.get(f"{RP}/e/status/j4").respond(200, json={"status": "COMPLETED", "output": {"cost": 0.1}})
    assert (await a.poll(route(path="output.video_url"), "e::j4")).error_class == ErrorClass.internal
    respx.post(f"{RP}/e/cancel/j5").respond(200, json={"id": "j5", "status": "CANCELLED"})
    assert await a.cancel(route(), "e::j5")

    respx.post(f"{RP}/e/run").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousSubmitError):
        await a.submit(route(), "e", {}, client_ref="c")
    respx.post(f"{RP}/bad/run").respond(401, json={"error": "unauthorized"})
    with pytest.raises(ProviderError) as err:
        await a.submit(route(), "bad", {}, client_ref="c")
    assert err.value.error_class == ErrorClass.route_unavailable


async def test_runpod_hosts_inputs_on_fal_or_inlines_small_images(runpod_key, tmp_path, monkeypatch):
    from studio.providers import factory
    from studio.providers.base import ResolvedMedia
    from studio.providers.runpod import RunPodAdapter

    img = tmp_path / "i.png"
    img.write_bytes(bytes([0x89]) + b"PNG" + b"0" * 64)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 64)
    monkeypatch.setattr(get_settings(), "fal_key", "")
    staged = await RunPodAdapter().stage_inputs(route(), [ResolvedMedia("start_image", "a", img, "image/png", "image")])
    assert staged["start_image"][0].startswith("data:image/png;base64,")
    with pytest.raises(ProviderError, match="FAL_KEY"):
        await RunPodAdapter().stage_inputs(route(), [ResolvedMedia("input_video", "v", clip, "video/mp4", "video")])

    class FakeFal:
        async def stage_inputs(self, route, media):
            return {m.role: [f"https://v3.fal.media/files/{m.path.name}"] for m in media}

    monkeypatch.setattr(get_settings(), "fal_key", "fal-key")
    monkeypatch.setattr(factory, "get_adapter", lambda key: FakeFal())
    staged = await RunPodAdapter().stage_inputs(route(), [ResolvedMedia("input_video", "v", clip, "video/mp4", "video")])
    assert staged["input_video"] == ["https://v3.fal.media/files/c.mp4"]


# ---------------------------------------------------------------- our own RunPod ComfyUI endpoints (wan-core)


@pytest.fixture
def wan_core(runpod_key, tmp_path, monkeypatch):
    """A RunPodComfyAdapter with a one-node template and a provider spec priced per GPU hour."""
    from studio.providers.runpod_comfy import RunPodComfyAdapter
    from studio.registry.schema import ProviderSpec

    (tmp_path / "t.json").write_text(json.dumps({
        "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "3": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "out"}},
    }))
    (tmp_path / "t.map.yaml").write_text(
        "inputs:\n  prompt: [{node: '2', input: text}]\n  start_image: [{node: '1', input: image}]\n"
        "  filename_prefix: [{node: '3', input: filename_prefix}]\noutputs:\n  - {node: '3', kind: video}\n")
    spec = ProviderSpec(id="p", name="Wan core", kind="selfhosted", adapter="runpod_comfy",
                        key_setting=["runpod_api_key", "runpod_wan_core_endpoint"],
                        endpoint_setting="runpod_wan_core_endpoint",
                        gpu_usd_per_hour={"RTX PRO 6000": 3.6, "H100": 7.2})
    monkeypatch.setattr(get_settings(), "runpod_wan_core_endpoint", "ep1")
    monkeypatch.setattr(get_settings(), "fal_key", "")  # no upload slots unless a test asks for them
    monkeypatch.setattr(RunPodComfyAdapter, "_spec", lambda self, route: spec)
    return RunPodComfyAdapter(workflows_dir=tmp_path)


@respx.mock
async def test_runpod_comfy_sends_the_rendered_workflow_and_prices_run_time(wan_core, tmp_path):
    import base64

    submit = respx.post(f"{RP}/ep1/run").respond(200, json={"id": "j1", "status": "IN_QUEUE"})
    video = base64.b64encode(b"mp4-bytes").decode()
    done = {"status": "COMPLETED", "executionTime": 100_000, "output": {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "seconds": 98.0,
        "outputs": [{"node": "3", "filename": "a.mp4", "kind": "video", "mime": "video/mp4", "data": video}]}}
    respx.get(f"{RP}/ep1/status/j1").mock(side_effect=[
        httpx.Response(200, json={"status": "IN_QUEUE", "delayTime": 4000}),
        httpx.Response(200, json=done), httpx.Response(200, json=done)])

    payload = {"prompt": "a fox", "start_image": "https://v3.fal.media/files/k.png#name=asset1.png",
               "inline": "data:image/png;base64,iVBO"}
    res = await wan_core.submit(route(endpoint="t"), "t", payload, client_ref="abcdef123456")
    body = json.loads(submit.calls.last.request.content)["input"]
    assert res.job_id == "ep1::j1"
    graph = body["workflow"]
    assert graph["1"]["inputs"]["image"] == "asset1.png" and graph["2"]["inputs"]["text"] == "a fox"
    assert graph["3"]["inputs"]["filename_prefix"].endswith("/abcdef12")
    assert body["inputs"][0] == {"name": "asset1.png", "url": "https://v3.fal.media/files/k.png"}

    assert (await wan_core.poll(route(), res.job_id)).status == "queued"
    finished = await wan_core.poll(route(), res.job_id)
    assert finished.status == "succeeded" and finished.outputs[0].ref == "ep1::j1#0"
    # 100 s on a PRO 6000 at $3.60/h = $0.10, flagged as an estimate (cold starts are billed per worker)
    assert finished.usage["cost_usd"] == pytest.approx(0.1) and finished.usage["cost_estimated"] is True

    dest = tmp_path / "out.mp4"
    mime = await wan_core.download(route(), finished.outputs[0], dest, tmp_path)
    assert dest.read_bytes() == b"mp4-bytes" and mime == "video/mp4"


@respx.mock
async def test_runpod_comfy_failures_still_report_gpu_time(wan_core, monkeypatch):
    respx.get(f"{RP}/ep1/status/j2").respond(200, json={
        "status": "FAILED", "executionTime": 36_000, "error": "KSamplerAdvanced: CUDA out of memory"})
    failed = await wan_core.poll(route(), "ep1::j2")
    # unknown GPU on failure -> priced at the dearest configured rate (H100 $7.20/h): 36 s = $0.072
    assert failed.status == "failed" and failed.usage["cost_usd"] == pytest.approx(0.072)
    respx.get(f"{RP}/ep1/status/j3").respond(200, json={"status": "COMPLETED", "output": {
        "error": "ComfyUI rejected the workflow: node 4 missing"}})
    assert (await wan_core.poll(route(), "ep1::j3")).error_class == ErrorClass.invalid_request

    monkeypatch.setattr(get_settings(), "runpod_wan_core_endpoint", "")
    with pytest.raises(ProviderError, match="RUNPOD_WAN_CORE_ENDPOINT"):
        await wan_core.submit(route(), "t", {"prompt": "x"}, client_ref="c")


@respx.mock
async def test_runpod_comfy_gives_big_results_an_upload_slot_and_ingests_from_it(wan_core, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "fal_key", "fal-key")
    initiate = respx.post(url__startswith="https://rest.fal.ai/storage/upload/initiate").respond(200, json={
        "upload_url": "https://v3b.fal.media/files/up/slot0?signature=s", "file_url": "https://v3b.fal.media/files/up/o.mp4"})
    submit = respx.post(f"{RP}/ep1/run").respond(200, json={"id": "j5", "status": "IN_QUEUE"})
    res = await wan_core.submit(route(endpoint="t"), "t", {"prompt": "a fox"}, client_ref="abcdef123456")

    # one slot per output the template saves, typed by its kind and kept a day
    sent = json.loads(initiate.calls.last.request.content)
    assert initiate.call_count == 1 and sent["content_type"] == "video/mp4" and sent["file_name"].endswith(".mp4")
    assert json.loads(initiate.calls.last.request.headers["X-Fal-Object-Lifecycle-Preference"]) == {
        "expiration_duration_seconds": 86_400}
    body = json.loads(submit.calls.last.request.content)["input"]
    assert body["uploads"] == [{"url": "https://v3b.fal.media/files/up/slot0?signature=s",
                                "file_url": "https://v3b.fal.media/files/up/o.mp4"}]
    assert res.raw["upload_slots"] == 1 and "signature" not in json.dumps(res.raw)  # the link isn't stored

    respx.get(f"{RP}/ep1/status/j5").respond(200, json={"status": "COMPLETED", "executionTime": 90_000, "output": {
        "gpu": "NVIDIA RTX PRO 6000", "outputs": [{"node": "3", "filename": "o.mp4", "kind": "video",
                                                  "mime": "video/mp4", "bytes": 48_000_000,
                                                  "url": "https://v3b.fal.media/files/up/o.mp4"}]}})
    done = await wan_core.poll(route(), "ep1::j5")
    out = done.outputs[0]
    assert done.status == "succeeded" and out.url == "https://v3b.fal.media/files/up/o.mp4" and out.meta["hosted"]
    respx.get("https://v3b.fal.media/files/up/o.mp4").respond(200, content=b"big-mp4",
                                                               headers={"content-type": "video/mp4"})
    dest = tmp_path / "o.mp4"
    assert await wan_core.download(route(), out, dest, tmp_path) == "video/mp4" and dest.read_bytes() == b"big-mp4"


@respx.mock
async def test_runpod_comfy_submits_without_slots_when_fal_cant_make_them(wan_core, monkeypatch):
    submit = respx.post(f"{RP}/ep1/run").respond(200, json={"id": "j6", "status": "IN_QUEUE"})
    await wan_core.submit(route(endpoint="t"), "t", {"prompt": "x"}, client_ref="c1")  # no FAL_KEY: no slots
    assert "uploads" not in json.loads(submit.calls.last.request.content)["input"]

    monkeypatch.setattr(get_settings(), "fal_key", "fal-key")
    respx.post(url__startswith="https://rest.fal.ai/storage/upload/initiate").respond(500, json={"detail": "down"})
    res = await wan_core.submit(route(endpoint="t"), "t", {"prompt": "x"}, client_ref="c2")
    assert res.job_id == "ep1::j6" and res.raw["upload_slots"] == 0
    assert "uploads" not in json.loads(submit.calls.last.request.content)["input"]


# ---------------------------------------------------------------- World Labs (Marble)

WL = "https://api.worldlabs.ai"


def marble():
    from studio.registry.loader import load_registry

    reg = load_registry(get_settings().resolve_registry_dir())
    spec = reg.model("marble")
    return spec, spec.routes[0]


@pytest.fixture
def wl_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "worldlabs_api_key", "wl-key")


def test_marble_payload_nests_the_world_prompt():
    from studio.registry.mapping import build_payload
    from studio.registry.validate import MediaRef, normalize

    spec, rt = marble()
    endpoint, payload, _ = build_payload(spec, rt, normalize(spec, "text_to_world", "a courtyard", {}, []), {})
    assert endpoint == "marble/v1/worlds:generate"
    assert payload["world_prompt"] == {"type": "text", "text_prompt": "a courtyard"}
    assert payload["model"] == "marble-1.1"
    req = normalize(spec, "image_to_world", "", {"model": "marble-1.0-draft"}, [MediaRef("input_image", "a", "image")])
    ref = {"source": "data_base64", "data_base64": "AAA=", "extension": "png"}
    _, payload, _ = build_payload(spec, rt, req, {"input_image": [ref]})
    assert payload["world_prompt"] == {"type": "image", "image_prompt": ref}


@respx.mock
async def test_marble_lifecycle_keeps_the_panorama_and_collider(wl_key):
    from studio.providers.worldlabs import WorldLabsAdapter

    _, rt = marble()
    submit = respx.post(f"{WL}/marble/v1/worlds:generate").respond(200, json={"operation_id": "op1"})
    status = respx.get(f"{WL}/marble/v1/operations/op1")
    world = {"world_id": "w1", "assets": {
        "splats": {"spz_urls": {"100k": "https://cdn.test/w1_100k.spz", "500k": "https://cdn.test/w1_500k.spz"},
                   "semantics_metadata": {"metric_scale_factor": 1.4, "ground_plane_offset": -0.2}},
        "imagery": {"pano_url": "https://cdn.test/w1_pano.png"},
        "mesh": {"collider_mesh_url": "https://cdn.test/w1_collider.glb"}}}
    # as seen live: the finished operation's copy of a draft world has no panorama yet; the stored world does
    lagging = {**world, "assets": {**world["assets"], "imagery": {"pano_url": None}}}
    status.side_effect = [
        httpx.Response(200, json={"operation_id": "op1", "done": False, "metadata": {}}),
        httpx.Response(200, json={"operation_id": "op1", "done": False,
                                  "metadata": {"progress": {"status": "IN_PROGRESS"}, "world_id": "w1"}}),
        httpx.Response(200, json={"operation_id": "op1", "done": True, "response": lagging,
                                  "cost": {"total_credits": 230, "line_items": []}}),
    ]
    respx.get(f"{WL}/marble/v1/worlds/w1").respond(200, json=world)
    a = WorldLabsAdapter()
    res = await a.submit(rt, "marble/v1/worlds:generate",
                         {"model": "marble-1.1", "world_prompt": {"type": "text", "text_prompt": "x" * 100}},
                         client_ref="abcdef123456")
    sent = submit.calls.last.request
    assert sent.headers["wlt-api-key"] == "wl-key"
    assert json.loads(sent.content)["display_name"] == "x" * 64
    assert res.job_id == "op1"
    assert (await a.poll(rt, "op1")).status == "queued"
    assert (await a.poll(rt, "op1")).status == "running"
    done = await a.poll(rt, "op1")
    assert done.status == "succeeded"
    kinds = [(o.kind, o.url.rsplit("/", 1)[1]) for o in done.outputs]
    assert kinds == [("splat", "w1_500k.spz"), ("image", "w1_pano.png"), ("model3d", "w1_collider.glb")]
    assert done.outputs[0].meta == {"world_id": "w1", "metric_scale_factor": 1.4, "ground_plane_offset": -0.2}
    assert done.outputs[1].meta == {"projection": "equirectangular", "world_id": "w1"}
    assert [o.index for o in done.outputs] == [0, 1, 2]
    assert done.usage == {"credits": 230, "cost_usd": 0.184}  # $1 buys 1,250 credits

    # if the world can't be read back, the operation's copy still gives the splat and collider
    status.side_effect = [httpx.Response(200, json={"operation_id": "op1", "done": True, "response": lagging})]
    respx.get(f"{WL}/marble/v1/worlds/w1").respond(503)
    fallback = await a.poll(rt, "op1")
    assert [o.kind for o in fallback.outputs] == ["splat", "model3d"] and fallback.usage == {}


@respx.mock
async def test_marble_failures(wl_key, monkeypatch):
    from studio.providers.worldlabs import WorldLabsAdapter

    _, rt = marble()
    respx.get(f"{WL}/marble/v1/operations/bad").respond(200, json={
        "done": True, "error": {"message": "Prompt violates the content policy"}})
    failed = await WorldLabsAdapter().poll(rt, "bad")
    assert failed.status == "failed" and failed.error_class == ErrorClass.moderation
    respx.get(f"{WL}/marble/v1/operations/empty").respond(200, json={"done": True, "response": {"assets": {}}})
    assert (await WorldLabsAdapter().poll(rt, "empty")).error_class == ErrorClass.internal
    respx.post(f"{WL}/marble/v1/worlds:generate").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AmbiguousSubmitError):
        await WorldLabsAdapter().submit(rt, "marble/v1/worlds:generate", {"world_prompt": {}}, client_ref="c")
    monkeypatch.setattr(get_settings(), "worldlabs_api_key", "")
    with pytest.raises(ProviderError) as exc:
        await WorldLabsAdapter().poll(rt, "x")
    assert exc.value.error_class == ErrorClass.route_unavailable


async def test_marble_inlines_pictures(wl_key, tmp_path):
    from studio.providers.base import ResolvedMedia
    from studio.providers.worldlabs import WorldLabsAdapter

    _, rt = marble()
    pic = tmp_path / "place.JPG"
    pic.write_bytes(b"\xff\xd8\xff" + b"0" * 50)
    staged = await WorldLabsAdapter().stage_inputs(rt, [ResolvedMedia("input_image", "a", pic, "image/jpeg", "image")])
    ref = staged["input_image"][0]
    assert ref["source"] == "data_base64" and ref["extension"] == "jpg" and ref["data_base64"].startswith("/9j/")


def test_route_outputs_need_the_main_file():
    from studio.providers.common import route_outputs
    from studio.registry.schema import ExtraOutput

    spec = OutputSpec(path="a.url", kind="image", meta={"projection": "equirectangular"},
                      extras=[ExtraOutput(path="b.url", kind="model3d")])
    assert route_outputs({"b": {"url": "https://x/b.glb"}}, spec) == []
    out = route_outputs({"a": {"url": "https://x/a.png"}, "b": {"url": "https://x/b.glb"}}, spec)
    assert [(o.index, o.kind, o.meta) for o in out] == [(0, "image", {"projection": "equirectangular"}),
                                                        (1, "model3d", {})]


def test_best_mime_keeps_3d_files():
    from studio.providers.common import best_mime

    assert best_mime(None, "application/octet-stream", kind="model3d") == "model/gltf-binary"
    assert best_mime(None, "model/gltf-binary", kind="model3d") == "model/gltf-binary"
    # a splat's type is vague; ingest then takes .spz or .ply from its URL
    assert best_mime(None, "binary/octet-stream", kind="splat") == "application/octet-stream"
    assert best_mime("application/x-ply", None, kind="splat") == "application/x-ply"
    assert best_mime(None, None, kind="video") == "video/mp4"


@respx.mock
async def test_fal_downloads_a_glb(keys, tmp_path):
    respx.get("https://v3.fal.media/files/x/model.glb").respond(200, content=b"glTF....",
                                                               headers={"content-type": "application/octet-stream"})
    out = ProviderOutput(index=0, kind="model3d", url="https://v3.fal.media/files/x/model.glb")
    mime = await FalAdapter().download(route(kind="model3d"), out, tmp_path / "m.bin", tmp_path)
    assert mime == "model/gltf-binary" and (tmp_path / "m.bin").read_bytes().startswith(b"glTF")


@respx.mock
async def test_runpod_comfy_adds_the_idle_tail_a_worker_is_billed_for(wan_core, monkeypatch):
    from studio.providers.runpod_comfy import RunPodComfyAdapter

    spec = wan_core._spec(route()).model_copy(update={"idle_seconds": 60})
    monkeypatch.setattr(RunPodComfyAdapter, "_spec", lambda self, r: spec)
    respx.get(f"{RP}/ep1/status/j4").respond(200, json={"status": "COMPLETED", "executionTime": 40_000, "output": {
        "gpu": "NVIDIA RTX PRO 6000", "outputs": []}})
    done = await wan_core.poll(route(), "ep1::j4")
    # 40 s of work + 60 s idle on a PRO 6000 at $3.60/h = $0.10
    assert done.usage["cost_usd"] == pytest.approx(0.1) and done.usage["idle_s"] == 60
