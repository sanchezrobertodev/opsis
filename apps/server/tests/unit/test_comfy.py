"""ComfyUI templates, the local adapter (against a fake ComfyUI), and local-route gating."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import httpx
import pytest

from studio.config import get_settings
from studio.domain.enums import ErrorClass
from studio.providers import comfy_events
from studio.providers.base import ProviderError, ProviderOutput, ResolvedMedia
from studio.providers.comfy_local import ComfyLocalAdapter
from studio.providers.comfy_workflows import load_template
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.routing import RoutingEnv, candidates, cheapest_live_estimate
from studio.registry.validate import MediaRef, normalize

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
        {"prompt": "cat", "start_image": ["opsis/a.png"], "bogus": 1}
    )
    assert graph["56"]["inputs"]["image"] == "opsis/a.png"
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



def test_a_lora_from_comfyui_is_optional_and_drops_out_of_the_chain():
    klein = REG.models["flux-2-klein-4b"]
    route = klein.route("flux-2-klein-4b@comfy_local")
    template = load_template(WORKFLOWS, "flux2_klein_4b")
    req = normalize(klein, "t2i", "harbor", {}, [])
    _, payload, _ = build_payload(klein, route, req, {})
    graph, warnings = template.render(payload)
    assert "90" not in graph and graph["7"]["inputs"]["model"] == ["1", 0] and not warnings
    # any file name passes (the form lists your ComfyUI's); its strength goes with it
    req = normalize(klein, "t2i", "harbor", {"lora": "pixel-art.safetensors", "lora_strength": 0.7}, [])
    _, payload, _ = build_payload(klein, route, req, {})
    graph, _ = template.render(payload)
    assert graph["7"]["inputs"]["model"] == ["90", 0]
    assert graph["90"]["inputs"] == {"model": ["1", 0], "lora_name": "pixel-art.safetensors", "strength_model": 0.7}


def test_a_bypass_must_name_a_link(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps({"1": {"class_type": "LoraLoaderModelOnly", "inputs": {
        "model": "not a link", "lora_name": ""}}, "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}}))
    (tmp_path / "w.map.yaml").write_text("inputs: {lora: [{node: '1', input: lora_name}]}\n"
                                         "optional: {lora: {node: '1', bypass: [model]}}\n"
                                         "outputs: [{node: '2', kind: image}]\n")
    with pytest.raises(ValueError, match="bypasses 'model'"):
        load_template(tmp_path, "w")


async def test_lora_choices_come_from_comfyui(monkeypatch):
    info = {"LoraLoaderModelOnly": {"input": {"required": {"lora_name": [["a.safetensors", "b.safetensors"]]}}}}

    async def object_info(self, classes):
        return {c: info[c] for c in classes if c in info}

    monkeypatch.setattr(ComfyLocalAdapter, "_object_info", object_info)
    a = adapter()
    assert await a.choices("LoraLoaderModelOnly", "lora_name") == ["a.safetensors", "b.safetensors"]
    assert await a.choices("NoSuchNode", "lora_name") is None


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
            return httpx.Response(200, json={"name": "abc.png", "subfolder": "opsis", "type": "input"})
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
                    "outputs": {"58": {"images": [{"filename": "clip_00001_.mp4", "subfolder": "opsis",
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

    async def no_socket(self):  # tests feed the listener's events themselves
        return None

    monkeypatch.setattr(ComfyLocalAdapter, "_client", client)
    monkeypatch.setattr(comfy_events.Listener, "_run", no_socket)
    monkeypatch.setattr(comfy_events, "_listeners", {})
    return fake


def adapter() -> ComfyLocalAdapter:
    return ComfyLocalAdapter(base_url="http://comfy.test", workflows_dir=WORKFLOWS)


async def test_full_job_lifecycle(fake, tmp_path):
    a = adapter()
    img = tmp_path / "frame.png"
    img.write_bytes(b"png")
    staged = await a.stage_inputs(ROUTE, [ResolvedMedia("start_image", "asset-1", img, "image/png", "image")])
    assert staged == {"start_image": ["opsis/abc.png"]}

    res = await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x", "start_image": staged["start_image"][0]},
                         client_ref="0123456789")
    queued = fake.prompts[res.job_id]
    assert queued["client_id"] == comfy_events.CLIENT_ID and queued["extra_data"] == {"preview_method": "auto"}
    assert queued["prompt"]["58"]["inputs"]["filename_prefix"].endswith("/01234567")

    poll = await a.poll(ROUTE, res.job_id)
    assert poll.status == "queued" and poll.queue_position == 1
    fake.state[res.job_id] = "running"
    running = await a.poll(ROUTE, res.job_id)
    assert running.status == "running" and running.progress is None  # no sampler has reported yet: estimated
    events = comfy_events.listener("http://comfy.test")
    (sampler,) = events.jobs[res.job_id].samplers
    events.handle(json.dumps({"type": "progress", "data": {"value": 15, "max": 30, "prompt_id": res.job_id,
                                                           "node": sampler}}))
    meta = json.dumps({"prompt_id": res.job_id, "image_type": "image/jpeg"}).encode()
    events.handle(struct.pack(">II", 4, len(meta)) + meta + b"jpeg-bytes")
    running = await a.poll(ROUTE, res.job_id)
    assert running.progress == 0.45 and running.preview == (b"jpeg-bytes", "image/jpeg")
    assert (await a.poll(ROUTE, res.job_id)).preview is None  # each frame is handed out once
    fake.state[res.job_id] = "done"
    done = await a.poll(ROUTE, res.job_id)
    assert done.status == "succeeded" and res.job_id not in events.jobs
    (out,) = done.outputs
    assert out.kind == "video" and out.mime == "video/mp4"

    dest = tmp_path / "out.mp4"
    assert await a.download(ROUTE, out, dest, tmp_path) == "video/mp4"
    assert dest.read_bytes().startswith(b"\x00\x00\x00 ftyp")


def test_progress_counts_every_sampler_and_previews_find_their_job():
    events = comfy_events.Listener("http://comfy.test")
    graph = {"3": {"class_type": "KSamplerAdvanced", "inputs": {"latent_image": ["5", 0]}},
             "4": {"class_type": "KSamplerAdvanced", "inputs": {"latent_image": ["3", 0]}},
             "8": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0]}}}
    assert comfy_events.samplers(graph) == ["3", "4"]
    events.jobs["p1"] = comfy_events._Job(samplers=["3", "4"])
    events.jobs["p2"] = comfy_events._Job(samplers=[])  # queued by another process: whatever reports counts
    events.handle(json.dumps({"type": "execution_cached", "data": {"nodes": ["3"], "prompt_id": "p1"}}))
    assert events.take("p1")[0] == 0.45
    events.handle(json.dumps({"type": "progress_state", "data": {"prompt_id": "p1", "nodes": {
        "4": {"value": 5, "max": 10, "state": "running"}, "8": {"value": 1, "max": 1}}}}))
    assert events.take("p1")[0] == 0.675  # the decoder's own progress doesn't count
    events.handle(json.dumps({"type": "progress", "data": {"value": 2, "max": 10, "prompt_id": "p1", "node": "4"}}))
    assert events.take("p1")[0] == 0.675  # never goes back
    events.handle(json.dumps({"type": "progress", "data": {"value": 4, "max": 8, "prompt_id": "p2", "node": "9"}}))
    assert events.take("p2")[0] == 0.45
    # an old-style preview names no job: it belongs to the one running now
    events.handle(json.dumps({"type": "executing", "data": {"node": "4", "prompt_id": "p1"}}))
    events.handle(struct.pack(">II", 1, 2) + b"png")
    assert events.take("p1") == (0.675, (b"png", "image/png"))
    events.handle(struct.pack(">II", 4, 3) + b"{x}" + b"bad metadata")
    events.handle(b"\x00")
    assert events.take("p1")[1] is None and events.take("nobody") == (None, None)


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
    assert "Start ComfyUI" in exc.value.message


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


# ---------------------------------------------------------------- wan-core (RunPod) templates and motion paths


def test_wan_core_fun_camera_maps_director_moves_to_camera_poses():
    spec = REG.models["wan-2.2-fun-camera"]
    route = spec.route("wan-2.2-fun-camera@wan_core")
    req = normalize(spec, "i2v", "harbor", {"camera_control": "TiltUp", "quality": "480p", "duration": 3,
                                            "aspect_ratio": "9:16"},
                    [MediaRef("start_image", "a", "image")])
    endpoint, payload, _ = build_payload(spec, route, req, {"start_image": ["frame.png"]})
    graph, warnings = load_template(WORKFLOWS, endpoint).render(payload)
    camera = graph["18"]["inputs"]
    assert camera["camera_pose"] == "Pan Up" and (camera["width"], camera["height"], camera["length"]) == (480, 832, 49)
    assert graph["12"]["inputs"]["width"] == ["18", 1]  # the video node reads its size from the camera node
    assert graph["11"]["inputs"]["image"] == "frame.png" and warnings == []


def test_wan_move_turns_drawn_paths_into_pixel_tracks():


    spec = REG.models["wan-move"]
    route = spec.route("wan-move@wan_core")
    paths = [{"points": [[0.1, 0.5], [0.9, 0.5]]}, {"points": [[0.5, 0.25]]}]
    req = normalize(spec, "i2v", "a boat", {"motion_paths": paths, "duration": 3}, [MediaRef("start_image", "a", "image")])
    endpoint, payload, _ = build_payload(spec, route, req, {"start_image": ["frame.png"]})
    assert "motion_paths" not in payload and (payload["width"], payload["height"], payload["length"]) == (832, 480, 49)
    graph, warnings = load_template(WORKFLOWS, endpoint).render(payload)
    tracks = json.loads(graph["22"]["inputs"]["track_coords"])
    assert len(tracks) == 10 and all(len(t) == 49 for t in tracks)  # 5 parallel tracks per stroke, one per frame
    centre = tracks[2]  # middle track of the first stroke: constant speed from x=83 to x=749 at y=240
    assert centre[0] == {"x": 83.2, "y": 240.0} and centre[-1] == {"x": 748.8, "y": 240.0}
    assert centre[24]["x"] == pytest.approx(416.0, abs=0.1)
    assert {(p["x"], p["y"]) for p in tracks[7]} == {(416.0, 120.0)}  # a pin holds still for the whole clip
    assert graph["19"]["inputs"]["width"] == graph["12"]["inputs"]["width"] == 832 and warnings == []


def test_motion_paths_are_validated():
    from studio.registry.validate import RequestError

    spec = REG.models["wan-move"]
    frame = [MediaRef("start_image", "a", "image")]
    for bad, message in [({}, "Draw at least one motion path"),
                         ({"motion_paths": []}, "Draw at least one motion path"),
                         ({"motion_paths": [{"points": [[1.2, 0.5]]}]}, "inside the frame"),
                         ({"motion_paths": [{"points": []}]}, "1 to 256 points"),
                         ({"motion_paths": [{"points": [[0.5, 0.5]]}] * 6}, "at most 5 paths")]:
        with pytest.raises(RequestError, match=message):
            normalize(spec, "i2v", "x", bad, frame)


def test_director_camera_moves_drive_fun_camera_natively():
    from studio.director.compiler import ShotDirection, compile_prompt
    from studio.domain.generations import merge_native

    spec = REG.models["wan-2.2-fun-camera"]
    pan = compile_prompt(spec, "harbor", ShotDirection.model_validate({"camera_moves": [{"id": "dolly_right"}]}))
    dolly = compile_prompt(spec, "harbor", ShotDirection.model_validate({"camera_moves": [{"id": "dolly_in"}]}))
    assert dolly.native_params == {"camera_control": "ZoomIn"}
    # The form always sends its default (ZoomIn); the camera move picked in the Director wins over it, silently.
    params, warnings = merge_native(spec, {"camera_control": "ZoomIn", "duration": 3}, pan.native_params)
    assert params == {"camera_control": "PanRight", "duration": 3} and warnings == []
    # A value the user deliberately picked is still replaced, but they're told.
    params, warnings = merge_native(spec, {"camera_control": "PanLeft"}, {"camera_control": "ZoomOut"})
    assert params["camera_control"] == "ZoomOut" and "replacing PanLeft" in warnings[0]
    # Orbit has a Fun Camera value but this model has no orbit preset: it stays a prompt-only move
    assert merge_native(spec, {"camera_control": "ZoomIn"}, {"camera_control": "Orbit"}) == ({"camera_control": "ZoomIn"}, [])
    assert merge_native(REG.models["wan-2.2-14b-fast"], {}, {"camera_control": "ZoomIn"}) == ({}, [])


def test_clip_driven_wan_core_workflows_keep_the_clips_shape_and_length():
    from types import SimpleNamespace as N

    from studio.providers.transforms.comfy import wan_clip_size

    route = N(media_fps={"input_video": 16, "driving_video": 16})  # the routes re-time clips to Wan's 16 fps

    def size(quality, *, w, h, ms, fps, role="input_video"):
        req = N(params={"quality": quality}, media=[N(role=role, width=w, height=h, duration_ms=ms, fps=fps)])
        out = wan_clip_size(spec=None, route=route, req=req, payload={})
        return out["width"], out["height"], out["length"]

    # a portrait 1080x1920 clip stays portrait at 480p's pixel budget, sides a multiple of 16; 3 s at 16 fps
    assert size("480p", w=1080, h=1920, ms=3000, fps=24) == (464, 832, 49)
    # a 5 s 30 fps clip is all covered: re-timed to 16 fps it's 81 frames, not its first 2.7 s
    assert size("720p", w=1920, h=1080, ms=5000, fps=30, role="driving_video") == (1280, 720, 81)
    assert size("480p", w=640, h=480, ms=200, fps=16)[2] == 5  # at least a few frames


async def test_wan_core_re_times_clips_the_route_names(monkeypatch, tmp_path):
    """Clips named in `media_fps` are re-encoded at that rate (same duration) and hosted afresh, not from the
    per-asset upload cache that holds the original; other inputs are staged as usual."""
    from types import SimpleNamespace as N

    from studio.config import get_settings
    from studio.media.ffmpeg import ffmpeg, probe
    from studio.providers import factory
    from studio.providers.base import ResolvedMedia
    from studio.providers.runpod_comfy import RunPodComfyAdapter

    clip = tmp_path / "clip.mp4"  # 2 s at 30 fps
    await ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2", "-pix_fmt", "yuv420p", str(clip))
    uploaded = []

    class Fal:
        class client:
            @staticmethod
            async def upload_file(path):
                info = await probe(path)
                uploaded.append((path.name, round(info.fps or 0), info.duration_ms))
                return "https://fal.media/retimed.mp4"

    monkeypatch.setattr(get_settings(), "fal_key", "k")
    monkeypatch.setattr(factory, "get_adapter", lambda key: Fal)
    adapter = RunPodComfyAdapter()

    async def original(route, m):
        return "https://fal.media/original.png"

    monkeypatch.setattr(adapter, "_public_url", original)
    route = N(media_fps={"video_references": 16})
    staged = await adapter.stage_inputs(route, [
        ResolvedMedia(role="video_references", asset_id="a1", path=clip, mime="video/mp4", kind="video"),
        ResolvedMedia(role="start_image", asset_id="a2", path=clip, mime="image/png", kind="image")])
    assert staged["video_references"] == ["https://fal.media/retimed.mp4#name=a1-16fps.mp4"]
    assert staged["start_image"][0].startswith("https://fal.media/original.png")
    assert uploaded == [("a1-16fps.mp4", 16, 2000)]


def test_ltx_payloads_fill_their_wan_core_templates():
    """LTX-2.5's three graphs get exactly the sizes they use: half size for the two-stage ones (plus the full size the
    start frame is fitted to), full size for first-and-last-frame; 24 fps lengths; sound off drops the audio decode."""
    spec = REG.models["ltx-2.5"]
    route = spec.route("ltx-2.5@wan_core")
    start, end = MediaRef(role="start_image", asset_id="a"), MediaRef(role="end_image", asset_id="b")
    cases = {"t2v": [], "i2v": [start], "flf2v": [start, end]}
    for mode, media in cases.items():
        req = normalize(spec, mode, "harbor", {"duration": 5, "aspect_ratio": "9:16"}, media)
        staged = {m.role: f"opsis/{m.asset_id}.png" for m in media}
        endpoint, payload, _ = build_payload(spec, route, req, staged)
        graph, warnings = load_template(WORKFLOWS, endpoint).render(payload)
        assert warnings == [], (mode, warnings)
        assert graph["9"]["inputs"]["length"] == graph["10"]["inputs"]["frames_number"] == 121  # 5 s at 24 fps + 1
        assert graph["6"]["inputs"]["text"] == "harbor"
        assert "27" in graph and graph["28"]["inputs"]["audio"] == ["27", 0]  # sound on by default
        size = (graph["9"]["inputs"]["width"], graph["9"]["inputs"]["height"])
        assert size == ((704, 1280) if mode == "flf2v" else (352, 640))
        if media:
            assert graph["30"]["inputs"]["image"] == "opsis/a.png"
            assert (graph["31"]["inputs"]["width"], graph["31"]["inputs"]["height"]) == (704, 1280)
        if mode == "flf2v":
            assert graph["35"]["inputs"]["image"] == "opsis/b.png"

    req = normalize(spec, "t2v", "harbor", {"generate_audio": False, "resolution": "1080p"}, [])
    endpoint, payload, _ = build_payload(spec, route, req, {})
    graph, warnings = load_template(WORKFLOWS, endpoint).render(payload)
    assert "27" not in graph and "audio" not in graph["28"]["inputs"] and warnings == []
    assert (graph["9"]["inputs"]["width"], graph["9"]["inputs"]["height"]) == (960, 544)  # 1920 x 1088 after x2


def test_ltx_fal_fallback_rounds_up_to_its_lengths_and_skips_square():
    spec = REG.models["ltx-2.5"]
    route = spec.route("ltx-2.5@fal")
    media = [MediaRef(role="start_image", asset_id="a"), MediaRef(role="end_image", asset_id="b")]
    req = normalize(spec, "flf2v", "dusk", {"duration": 5, "seed": 3}, media)
    endpoint, payload, _ = build_payload(spec, route, req, {"start_image": "https://x/a.png", "end_image": "https://x/b.png"})
    assert endpoint == "lightricks/ltx-2.5/image-to-video/fast"
    assert payload["duration"] == 6 and "seed" not in payload and "negative_prompt" not in payload
    assert payload["end_image_url"] == "https://x/b.png"
    env = RoutingEnv(provider_mode="live", live_allowlist=set(), has_key=lambda _p: True, has_adapter=lambda _a: True)
    by_id = {c.route.id: c for c in candidates(REG, spec, req, env)}
    assert float(by_id["ltx-2.5@fal"].estimate.usd) == pytest.approx(6 * 0.09)  # billed for the 6 s it renders
    square = normalize(spec, "t2v", "dusk", {"aspect_ratio": "1:1"}, [])
    fal = next(c for c in candidates(REG, spec, square, env) if c.route.id == "ltx-2.5@fal")
    assert not fal.eligible and "aspect_ratio" in (fal.reason or "")


# ---------------------------------------------------------------- the wan-core graphs on your own ComfyUI


def test_missing_model_files_and_nodes_are_found_from_object_info():
    """Fixed choices the graph makes that this ComfyUI doesn't offer are model files it hasn't got; inputs the map
    fills (the clips) are the user's, never flagged."""
    from studio.providers.comfy_workflows import model_files, unavailable_choices

    template = load_template(WORKFLOWS, "runpod_wan21_vace_edit")
    have = {"wan2.1_vace_14B_fp16.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"}
    info = {}
    for node in template.graph.values():
        inputs = {name: ([sorted(have)] if isinstance(v, str) and v.endswith(".safetensors") else ["STRING", {}])
                  for name, v in node["inputs"].items()}
        info[node["class_type"]] = {"input": {"required": inputs}}
    classes, missing = unavailable_choices(template, info, model_files())
    assert classes == []
    assert sorted(missing) == ["Wan21_CausVid_14B_T2V_lora_rank32.safetensors", "wan_2.1_vae.safetensors"]
    info.pop("LoadVideo")
    assert unavailable_choices(template, info, model_files())[0] == ["LoadVideo"]
    # the newer COMBO format lists its options the same way
    info["LoadVideo"] = {"input": {"required": {"file": ["COMBO", {"options": []}]}}}
    assert unavailable_choices(template, info, model_files())[0] == []


def test_download_groups_skip_the_14b_experts_a_graph_doesnt_use():
    from studio.providers.comfy_workflows import download_groups, model_files

    files = model_files()
    vae, encoder = "wan_2.1_vae.safetensors", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
    assert download_groups(["wan2.1_vace_14B_fp16.safetensors", vae, encoder], files) == ["vace", "wan-shared"]
    assert download_groups(["wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", vae, encoder], files) == ["core"]
    assert download_groups(["wan2.2_ti2v_5B_fp16.safetensors", encoder], files) == ["wan5b"]


def test_every_model_file_a_workflow_names_can_be_downloaded():
    from studio.providers.comfy_workflows import model_files

    files = model_files()
    named = {v for path in WORKFLOWS.glob("*.json") for node in json.loads(path.read_text(encoding="utf-8")).values()
             for v in node.get("inputs", {}).values()
             if isinstance(v, str) and v.endswith((".safetensors", ".pth", ".ckpt", ".gguf"))}
    assert named and named <= set(files), sorted(named - set(files))


def test_fetcher_keeps_local_only_files_off_the_volume_and_fetches_shared_files_once():
    import importlib.util

    from studio.config import REPO_ROOT

    path = REPO_ROOT / "infra" / "runpod" / "wan-core" / "fetch_models.py"
    module_spec = importlib.util.spec_from_file_location("fetch_models", path)
    fetch = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(fetch)
    volume = fetch.load(fetch.MANIFEST)
    assert not any(f.get("local_only") for f in volume)
    assert {f.get("group", "core") for f in volume} == {"core", "camera", "move", "vace", "animate2", "uni3c", "ltx"}
    pc = fetch.load(fetch.MANIFEST, ["core", "wan5b", "wan-shared"], local=True)
    names = [f["name"] for f in pc]
    assert len(names) == len(set(names)) and "wan2.2_ti2v_5B_fp16.safetensors" in names


async def test_a_small_gpu_or_missing_files_keep_a_local_route_out(monkeypatch):
    from studio.providers import local_status

    spec = REG.models["ltx-2.5"]
    route = spec.route("ltx-2.5@comfy_local")
    a = adapter()

    async def stats(_url):
        return {"devices": [{"vram_total": int(11.9 * 2**30)}]}

    monkeypatch.setattr(local_status, "comfy_stats", stats)
    assert await a.unavailable(route, "t2v") == "needs a GPU with 24 GB of memory; ComfyUI's has 12 GB"

    async def big(_url):
        return {"devices": [{"vram_total": int(23.99 * 2**30)}]}  # a 24 GB card reports a little under

    async def info(classes):
        return {c: {"input": {"required": {}}} for c in classes} | {
            "UNETLoader": {"input": {"required": {"unet_name": [[], {}]}}}}

    monkeypatch.setattr(local_status, "comfy_stats", big)
    monkeypatch.setattr(a, "_object_info", info)
    why = await a.unavailable(route, "t2v")
    assert why.startswith("ComfyUI is missing ltx-2.5-22b-distilled") and "--group ltx" in why
    monkeypatch.setattr(a, "_object_info", lambda classes: _done({c: {"input": {"required": {}}} for c in classes}))
    assert await a.unavailable(route, "t2v") is None


async def _done(value):
    return value


async def test_local_runs_re_time_clips_and_take_a_fresh_seed(fake, monkeypatch, tmp_path):
    from studio.providers import comfy_local

    retimed = []

    async def retime(src, fps, dest):
        retimed.append((src.name, fps))
        dest.write_bytes(b"mp4")

    monkeypatch.setattr(comfy_local, "retime_clip", retime)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"mp4")
    route = REG.models["wan-vace"].route("wan-vace@comfy_local")
    staged = await adapter().stage_inputs(route, [ResolvedMedia("input_video", "v1", clip, "video/mp4", "video")])
    assert retimed == [("clip.mp4", 16)] and staged == {"input_video": ["opsis/abc.png"]} and fake.uploads

    a = adapter()
    seeds = set()
    for _ in range(2):
        res = await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x"}, client_ref="r")
        seeds.add(fake.prompts[res.job_id]["prompt"]["3"]["inputs"]["seed"])
        assert res.raw["seed"] in seeds
    assert len(seeds) == 2 and 50702026 not in seeds  # not the seed the template was saved with
    res = await a.submit(ROUTE, "wan22_ti2v_5b", {"prompt": "x", "seed": 7}, client_ref="r")
    assert fake.prompts[res.job_id]["prompt"]["3"]["inputs"]["seed"] == 7


def ltx_env(*, online: bool, blocked: str | None, mode="mock") -> RoutingEnv:
    return RoutingEnv(provider_mode=mode, live_allowlist=set(), has_key=lambda p: p == "comfy_local" or mode == "live",
                      has_adapter=lambda a: True, reachable=lambda p: online,
                      blocked=lambda r, m: blocked if r.provider == "comfy_local" else None)


def test_a_model_that_also_runs_elsewhere_uses_the_local_engine_only_when_it_can():
    spec = REG.models["ltx-2.5"]
    req = normalize(spec, "t2v", "x", {}, [])

    def cands(e):
        return {c.route.id: c for c in candidates(REG, spec, req, e)}

    ready = cands(ltx_env(online=True, blocked=None))
    assert [k for k, c in ready.items() if c.eligible] == ["ltx-2.5@comfy_local"]  # mock mode: the real thing, free
    live = [c.route.id for c in candidates(REG, spec, req, ltx_env(online=True, blocked=None, mode="live")) if c.eligible]
    assert live[0] == "ltx-2.5@comfy_local" and "ltx-2.5@wan_core" in live  # free first, RunPod behind it

    missing = cands(ltx_env(online=True, blocked="ComfyUI is missing ltx.safetensors"))
    assert missing["ltx-2.5@comfy_local"].reason == "ComfyUI is missing ltx.safetensors"
    assert missing["ltx-2.5@mock"].eligible  # unlike Wan 2.2 5B, it has somewhere else to run: the mock stands in
    assert cands(ltx_env(online=False, blocked=None))["ltx-2.5@mock"].eligible
    assert cheapest_live_estimate(list(missing.values())).route.id != "ltx-2.5@comfy_local"


def test_each_preview_frame_replaces_the_last_and_leaves_with_the_take():
    import uuid

    from studio.db.models import Generation
    from studio.domain import fsm
    from studio.storage.local import get_storage
    from studio.worker.tasks.generation import _store_preview

    gen = Generation(id=uuid.uuid4(), user_id=uuid.uuid4())
    storage = get_storage()
    first = _store_preview(fsm.preview_prefix(gen), None, b"one", "image/jpeg")
    second = _store_preview(fsm.preview_prefix(gen), first, b"two", "image/png")
    assert first != second and second.endswith(".png")  # a new name each time: /media is cached per key
    assert not storage.exists(first) and storage.path_for(second).read_bytes() == b"two"
    gen.preview_key = second
    assert fsm.preview_url(gen) == storage.url_for(second)
    fsm.drop_preview(gen)
    assert gen.preview_key is None and not storage.exists(fsm.preview_prefix(gen))


def test_choices_endpoint_says_when_comfyui_is_off(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.api.deps import current_user
    from studio.api.routers import local_engine

    async def choices(self, node, input_):
        return ["a.safetensors"] if self.base == "http://up.test" else None

    monkeypatch.setattr(ComfyLocalAdapter, "choices", choices)
    app = FastAPI()
    app.include_router(local_engine.router)
    app.dependency_overrides[current_user] = lambda: object()
    client = TestClient(app)
    monkeypatch.setattr(get_settings(), "comfyui_url", "http://up.test")
    assert client.get("/local-engine/choices", params={"field": "LoraLoaderModelOnly.lora_name"}).json() == {
        "available": True, "options": ["a.safetensors"], "message": None}
    monkeypatch.setattr(get_settings(), "comfyui_url", "http://down.test")
    off = client.get("/local-engine/choices", params={"field": "LoraLoaderModelOnly.lora_name"}).json()
    assert not off["available"] and "Is it running?" in off["message"]
    assert client.get("/local-engine/choices", params={"field": "../etc"}).status_code == 422
