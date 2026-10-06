"""ComfyUI templates, the local adapter (against a fake ComfyUI), and local-route gating."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers.base import ProviderError, ProviderOutput, ResolvedMedia
from studio.providers.comfy_local import ComfyLocalAdapter
from studio.providers.comfy_workflows import load_template
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.routing import RoutingEnv, candidates
from studio.registry.validate import normalize

WORKFLOWS = get_settings().comfyui_workflows_dir
REG = load_registry(get_settings().resolve_registry_dir())
WAN = REG.models["wan-2.2-5b"]
ROUTE = WAN.route("wan-2.2-5b@comfy_local")


# ---------------------------------------------------------------- templates


def test_wan_template_text_to_video_drops_start_image():
    graph, warnings = load_template(WORKFLOWS, "wan22_ti2v_5b").render(
        {"prompt": "boat", "seed": 7, "length": 73, "width": 768, "height": 448, "negative_prompt": "blurry"}
    )
    assert "56" not in graph
    assert "start_image" not in graph["55"]["inputs"]
    assert graph["55"]["inputs"] | {"vae": None} == {"width": 768, "height": 448, "length": 73, "batch_size": 1, "vae": None}
    assert graph["6"]["inputs"]["text"] == "boat"
    assert graph["3"]["inputs"]["seed"] == 7
    assert graph["7"]["inputs"]["text"].endswith(", blurry")  # appended to Wan's official negative
    assert graph["8"]["class_type"] == "VAEDecodeTiled"
    assert warnings == []


def test_wan_template_image_to_video_keeps_loader_and_reports_unknown_keys():
    graph, warnings = load_template(WORKFLOWS, "wan22_ti2v_5b").render(
        {"prompt": "cat", "start_image": ["ai-media-studio/a.png"], "bogus": 1}
    )
    assert graph["56"]["inputs"]["image"] == "ai-media-studio/a.png"
    assert graph["55"]["inputs"]["start_image"] == ["56", 0]
    assert any("bogus" in w for w in warnings)


def test_flux_template_sets_both_size_nodes():
    graph, _ = load_template(WORKFLOWS, "flux2_klein_4b").render({"prompt": "ramen", "width": 1216, "height": 688,
                                                                  "num_images": 2})
    assert graph["9"]["inputs"]["width"] == graph["10"]["inputs"]["width"] == 1216
    assert graph["10"]["inputs"]["batch_size"] == 2


def test_bad_map_is_rejected(tmp_path: Path):
    (tmp_path / "x.json").write_text(json.dumps({"1": {"class_type": "A", "inputs": {"a": 1}}}))
    (tmp_path / "x.map.yaml").write_text("inputs:\n  p: [{node: '9', input: a}]\noutputs: [{node: '1', kind: image}]\n")
    with pytest.raises(ValueError, match="missing node 9"):
        load_template(tmp_path, "x")


def test_registry_maps_canonical_params_onto_the_template():
    req = normalize(WAN, "t2v", "harbor", {"quality": "standard", "duration": 5, "aspect_ratio": "9:16"}, [])
    endpoint, payload, _ = build_payload(WAN, ROUTE, req, {})
    assert endpoint == "wan22_ti2v_5b"
    assert payload["length"] == 121  # 5 s at 24 fps, plus Wan's extra frame
    assert (payload["width"], payload["height"]) == (704, 1280)
    assert "quality" not in payload and "aspect_ratio" not in payload


# ---------------------------------------------------------------- adapter against a fake ComfyUI


class FakeComfy:
    def __init__(self) -> None:
        self.prompts: dict[str, dict] = {}
        self.state: dict[str, str] = {}  # id -> pending | running | done | error
        self.uploads: list[str] = []
        self.deleted: list[str] = []
        self.interrupted = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/upload/image":
            self.uploads.append("x")
            return httpx.Response(200, json={"name": "abc.png", "subfolder": "ai-media-studio", "type": "input"})
        if path == "/prompt":
            body = json.loads(request.content)
            if "UNETLoader" in json.dumps(body) and "missing" in json.dumps(body):
                return httpx.Response(400, json={"error": {"message": "Prompt outputs failed validation"},
                                                 "node_errors": {"37": {"class_type": "UNETLoader", "errors": [
                                                     {"message": "Value not in list", "details": "unet_name"}]}}})
            pid = f"p{len(self.prompts) + 1}"
            self.prompts[pid] = body
            self.state[pid] = "pending"
            return httpx.Response(200, json={"prompt_id": pid, "number": len(self.prompts), "node_errors": {}})
        if path == "/queue" and request.method == "GET":
            running = [[1, p, {}, {}, ["58"]] for p, st in self.state.items() if st == "running"]
            pending = [[2, p, {}, {}, ["58"]] for p, st in self.state.items() if st == "pending"]
            return httpx.Response(200, json={"queue_running": running, "queue_pending": pending})
        if path == "/queue" and request.method == "POST":
            self.deleted += json.loads(request.content)["delete"]
            return httpx.Response(200, json={})
        if path == "/interrupt":
            self.interrupted = True
            return httpx.Response(200, json={})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            st = self.state.get(pid)
            if st == "done":
                return httpx.Response(200, json={pid: {
                    "prompt": [1, pid, {}, {}, ["58"]],
                    "outputs": {"58": {"images": [{"filename": "clip_00001_.mp4", "subfolder": "ai-media-studio",
                                                   "type": "output"}], "animated": [True]}},
                    "status": {"status_str": "success", "completed": True, "messages": []},
                }})
            if st == "error":
                return httpx.Response(200, json={pid: {"prompt": [1, pid, {}, {}, ["58"]], "outputs": {}, "status": {
                    "status_str": "error", "completed": False, "messages": [["execution_error", {
                        "node_type": "VAEDecodeTiled", "exception_message": "CUDA out of memory."}]]}}})
            return httpx.Response(200, json={})
        if path == "/view":
            return httpx.Response(200, content=b"\x00\x00\x00 ftypmp42 fake-mp4-bytes")
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeComfy()

    def client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(ComfyLocalAdapter, "_client", client)
    return fake


def adapter() -> ComfyLocalAdapter:
    return ComfyLocalAdapter(base_url="http://comfy.test", workflows_dir=WORKFLOWS)


async def test_full_job_lifecycle(fake, tmp_path):
    a = adapter()
    img = tmp_path / "frame.png"
    img.write_bytes(b"png")
    staged = await a.stage_inputs(ROUTE, [ResolvedMedia("start_image", "asset-1", img, "image/png", "image")])
    assert staged == {"start_image": ["ai-media-studio/abc.png"]}

    res = await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x", "start_image": staged["start_image"][0]},
                         client_ref="0123456789")
    queued = fake.prompts[res.job_id]
    assert queued["client_id"] == "ai-media-studio"
    assert queued["prompt"]["58"]["inputs"]["filename_prefix"].endswith("/01234567")

    poll = await a.poll(ROUTE, res.job_id)
    assert poll.status == "queued" and poll.queue_position == 1
    fake.state[res.job_id] = "running"
    assert (await a.poll(ROUTE, res.job_id)).status == "running"
    fake.state[res.job_id] = "done"
    done = await a.poll(ROUTE, res.job_id)
    assert done.status == "succeeded"
    (out,) = done.outputs
    assert out.kind == "video" and out.mime == "video/mp4"

    dest = tmp_path / "out.mp4"
    assert await a.download(ROUTE, out, dest, tmp_path) == "video/mp4"
    assert dest.read_bytes().startswith(b"\x00\x00\x00 ftyp")


async def test_out_of_memory_explains_what_to_do(fake):
    a = adapter()
    res = await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x"}, client_ref="r")
    fake.state[res.job_id] = "error"
    poll = await a.poll(ROUTE, res.job_id)
    assert poll.status == "failed"
    assert "out of memory" in poll.error_message and "draft quality" in poll.error_message


async def test_lost_job_is_route_unavailable(fake):
    poll = await adapter().poll(ROUTE, "never-queued")
    assert poll.status == "failed" and poll.error_class == ErrorClass.route_unavailable


async def test_validation_errors_are_invalid_requests(fake, monkeypatch):
    with pytest.raises(ProviderError) as exc:
        await adapter().submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x", "negative_prompt": "missing"}, client_ref="r")
    assert exc.value.error_class == ErrorClass.invalid_request
    assert "Value not in list" in exc.value.message


async def test_cancel_pending_deletes_and_running_interrupts(fake):
    a = adapter()
    p1 = (await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x"}, client_ref="r")).job_id
    assert await a.cancel(ROUTE, p1)
    assert fake.deleted == [p1]
    p2 = (await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "y"}, client_ref="r")).job_id
    fake.state[p2] = "running"
    assert await a.cancel(ROUTE, p2)
    assert fake.interrupted


async def test_offline_engine_is_route_unavailable_with_instructions(monkeypatch):
    a = ComfyLocalAdapter(base_url="http://127.0.0.1:9", workflows_dir=WORKFLOWS)  # nothing listens on port 9
    with pytest.raises(ProviderError) as exc:
        await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x"}, client_ref="r")
    assert exc.value.error_class == ErrorClass.route_unavailable
    assert "Start Local Video Studio" in exc.value.message


async def test_download_rejects_missing_file(monkeypatch, tmp_path):
    def client(self, timeout: float = 30.0):
        return httpx.AsyncClient(base_url="http://comfy.test",
                                 transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    monkeypatch.setattr(ComfyLocalAdapter, "_client", client)
    out = ProviderOutput(index=0, kind="video", ref=json.dumps({"filename": "gone.mp4"}))
    with pytest.raises(ProviderError):
        await adapter().download(ROUTE, out, tmp_path / "x.mp4", tmp_path)


# ---------------------------------------------------------------- routing


def env(*, url: bool, online: bool, mode="mock") -> RoutingEnv:
    return RoutingEnv(provider_mode=mode, live_allowlist=set(), has_key=lambda p: url if p == "comfy_local" else False,
                      has_adapter=lambda a: True, reachable=lambda p: online)


def eligible(e: RoutingEnv) -> list[str]:
    req = normalize(WAN, "t2v", "x", {}, [])
    return [c.route.id for c in candidates(REG, WAN, req, e) if c.eligible]


def test_local_route_runs_even_in_mock_mode_and_suppresses_the_mock():
    assert eligible(env(url=True, online=True)) == ["wan-2.2-5b@comfy_local"]
    assert eligible(env(url=True, online=True, mode="live")) == ["wan-2.2-5b@comfy_local"]


def test_offline_engine_never_falls_back_to_test_patterns():
    req = normalize(WAN, "t2v", "x", {}, [])
    cands = {c.route.id: c for c in candidates(REG, WAN, req, env(url=True, online=False))}
    assert not any(c.eligible for c in cands.values())
    assert "isn't running" in cands["wan-2.2-5b@comfy_local"].reason
    assert cands["wan-2.2-5b@mock"].reason == "this model runs on the local engine"


def test_unconfigured_engine_uses_the_mock():
    assert eligible(env(url=False, online=False)) == ["wan-2.2-5b@mock"]


def test_local_routes_report_an_eta():
    req = normalize(WAN, "t2v", "x", {"quality": "draft", "duration": 3}, [])
    (local,) = [c for c in candidates(REG, WAN, req, env(url=True, online=True)) if c.eligible]
    assert local.eta_seconds == pytest.approx(70)
