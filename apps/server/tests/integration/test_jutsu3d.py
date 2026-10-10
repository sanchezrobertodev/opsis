"""3D blocking scenes: versioned project documents, and render passes uploaded frame by frame from the browser."""

from __future__ import annotations

import io
import uuid

import httpx
import pytest
from PIL import Image

from studio.api.main import app as api
from studio.db.models import Generation
from studio.db.session import session_scope
from studio.media.mock_media import make_glb


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


def jpeg(shade: int, size: tuple[int, int] = (128, 72)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buf, "JPEG")
    return buf.getvalue()


async def new_scene(client) -> dict:
    r = await client.post("/projects", json={"kind": "jutsu3d", "title": "Rooftop chase", "document": {}})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_new_scene_starts_with_camera_figure_and_sun_and_saves_with_revisions(client):
    p = await new_scene(client)
    doc = p["document"]
    assert [o["kind"] for o in doc["objects"]] == ["camera", "mannequin", "sun"]
    assert doc["active_camera_id"] == doc["objects"][0]["id"]

    cam = doc["objects"][0]["id"]
    keyed = {**doc, "timeline": {**doc["timeline"], "duration": 3, "tracks": [{"object_id": cam, "keys": [
        {"t": 2, "position": [2, 1.6, 4]}, {"t": 0, "position": [0, 1.6, 5], "focal_length": 35}]}]}}
    r = await client.put(f"/projects/{p['id']}", json={"version": 1, "document": keyed})
    assert r.status_code == 200, r.text
    assert [k["t"] for k in r.json()["document"]["timeline"]["tracks"][0]["keys"]] == [0, 2]  # kept in order

    bad = {**doc, "timeline": {**doc["timeline"], "tracks": [{"object_id": "onobody", "keys": []}]}}
    r = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": bad})
    assert r.status_code == 422 and "tracks for objects not in the scene" in r.text
    r = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": {
        **doc, "objects": [*doc["objects"], {"kind": "model", "asset_id": None}]}})
    assert r.status_code == 422 and "needs its GLB asset" in r.text
    listed = (await client.get("/projects", params={"kind": "jutsu3d"})).json()
    assert [(s["title"], s["objects"]) for s in listed] == [("Rooftop chase", 3)]


async def test_render_passes_become_assets_tied_to_the_scene_version(client):
    p = await new_scene(client)
    r = await client.post(f"/projects/{p['id']}/renders", json={
        "version": 1, "passes": ["beauty", "depth"], "fps": 12, "frames": 6, "width": 128, "height": 72})
    assert r.status_code == 201, r.text
    rid = r.json()["render_id"]
    base = f"/projects/{p['id']}/renders/{rid}"
    for render_pass in ("beauty", "depth"):
        for offset in (0, 3):
            files = [("files", (f"{i}.jpg", jpeg(40 * i), "image/jpeg")) for i in range(3)]
            r = await client.post(f"{base}/frames", params={"pass": render_pass, "start": offset}, files=files)
            assert r.status_code == 204, r.text
    too_many = await client.post(f"{base}/frames", params={"pass": "beauty", "start": 5},
                                 files=[("files", ("a.jpg", jpeg(1), "image/jpeg"))] * 2)
    assert too_many.status_code == 422

    r = await client.post(f"{base}/finish")
    assert r.status_code == 200, r.text
    assets = r.json()["assets"]
    assert set(assets) == {"beauty", "depth"}
    beauty = assets["beauty"]
    assert beauty["kind"] == "video" and abs(beauty["duration_ms"] - 500) < 100
    assert beauty["metadata"]["scene_version"] == 1 and beauty["metadata"]["pass"] == "beauty"
    assert (await client.post(f"{base}/finish")).status_code == 404  # frames are gone once finished


async def test_a_one_frame_render_is_a_still_and_missing_frames_are_refused(client):
    p = await new_scene(client)
    rid = (await client.post(f"/projects/{p['id']}/renders", json={
        "version": 1, "passes": ["beauty"], "frames": 1, "width": 128, "height": 72, "times": [1.5]})).json()["render_id"]
    base = f"/projects/{p['id']}/renders/{rid}"
    early = await client.post(f"{base}/finish")
    assert early.status_code == 422 and "0 of 1 frames" in early.text
    await client.post(f"{base}/frames", params={"pass": "beauty"}, files=[("files", ("a.jpg", jpeg(90), "image/jpeg"))])
    still = (await client.post(f"{base}/finish")).json()["stills"]["beauty"][0]
    assert still["kind"] == "image" and still["metadata"]["time"] == 1.5

    film = (await client.post("/projects", json={"title": "A film", "document": {}})).json()
    r = await client.post(f"/projects/{film['id']}/renders", json={
        "version": 1, "passes": ["beauty"], "frames": 1, "width": 128, "height": 72})
    assert r.status_code == 404  # only 3D scenes render


# ---------------------------------------------------------------- making it real (the jutsu3d recipe)


async def render(client, p: dict, *, frames: int, times: list[float] | None = None, fps: int = 12) -> dict:
    body = {"version": 1, "passes": ["beauty", "depth"] if times is None else ["beauty"], "fps": fps,
            "frames": frames, "width": 128, "height": 72, **({"times": times} if times is not None else {})}
    rid = (await client.post(f"/projects/{p['id']}/renders", json=body)).json()["render_id"]
    base = f"/projects/{p['id']}/renders/{rid}"
    for render_pass in body["passes"]:
        files = [("files", (f"{i}.jpg", jpeg(30 * i + 20), "image/jpeg")) for i in range(frames)]
        assert (await client.post(f"{base}/frames", params={"pass": render_pass}, files=files)).status_code == 204
    return (await client.post(f"{base}/finish")).json()


async def test_angles_are_redrawn_then_animated_between_and_cut(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, get, start  # importable once the suite is collected

    p = await new_scene(client)
    stills = (await render(client, p, frames=3, times=[0, 2, 4]))["stills"]["beauty"]
    picks = [{"asset_id": s["id"], "time": s["metadata"]["time"]} for s in stills]

    unordered = await client.post("/pipelines", json={"recipe": "jutsu3d", "input": {
        "project_id": p["id"], "model_id": "kling-3.0", "prompt": "x", "stills": picks[::-1]}})
    assert unordered.status_code == 422 and "time order" in unordered.text
    no_end = await client.post("/pipelines", json={"recipe": "jutsu3d", "input": {
        "project_id": p["id"], "model_id": "wan-vace-depth", "prompt": "x", "stills": picks}})
    assert no_end.status_code == 422 and "start and end frames" in no_end.text

    run = await start(client, "jutsu3d", project_id=p["id"], model_id="kling-3.0", stills=picks,
                      prompt="A rain-soaked rooftop at night, a courier in a yellow jacket",
                      params={"aspect_ratio": "16:9"})
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"look[00]", "look[01]", "look[02]", "clip[00]", "clip[01]", "cut"}
    async with session_scope() as s:
        looks = [await s.get(Generation, uuid.UUID(steps[f"look[0{i}]"]["generation_id"])) for i in range(3)]
        clips = [await s.get(Generation, uuid.UUID(steps[f"clip[0{i}]"]["generation_id"])) for i in range(2)]
    assert [g.request["media"][0]["asset_id"] for g in looks] == [s["id"] for s in stills]  # the still leads
    assert all(str(g.project_id) == p["id"] for g in looks + clips)
    frames = done["output"]["looks"]
    assert [[m["role"] for m in g.request["media"]] for g in clips] == [["start_image", "end_image"]] * 2
    assert [m["asset_id"] for m in clips[1].request["media"]] == frames[1:]
    assert clips[0].request["params"]["duration"] >= 2
    assert done["output"]["asset_id"] == steps["cut"]["output"]["asset_id"]
    listed = (await client.get("/pipelines", params={"project_id": p["id"]})).json()
    assert [r["id"] for r in listed] == [run["id"]]


async def test_the_depth_pass_drives_a_depth_model_from_a_redrawn_opening_frame(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, get, start

    p = await new_scene(client)
    depth = (await render(client, p, frames=12))["assets"]["depth"]
    opening = (await render(client, p, frames=1, times=[0]))["stills"]["beauty"][0]
    wrong = await client.post("/pipelines", json={"recipe": "jutsu3d", "input": {
        "project_id": p["id"], "path": "depth", "model_id": "kling-3.0", "prompt": "x", "depth_asset_id": depth["id"]}})
    assert wrong.status_code == 422 and "depth-to-video" in wrong.text

    est = await client.post("/pipelines/estimate", json={"recipe": "jutsu3d", "input": {
        "project_id": p["id"], "path": "depth", "model_id": "wan-vace-depth", "prompt": "x",
        "depth_asset_id": depth["id"], "params": {"resolution": "480p"}}})
    assert est.status_code == 200 and est.json()["live_estimate_usd"] == pytest.approx(12 / 16 * 0.04, abs=0.01)

    run = await start(client, "jutsu3d", project_id=p["id"], path="depth", model_id="wan-vace-depth",
                      prompt="A misty forest path at dawn", depth_asset_id=depth["id"],
                      stills=[{"asset_id": opening["id"], "time": 0}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        clip = await s.get(Generation, uuid.UUID(steps["clip[00]"]["generation_id"]))
    media = {m["role"]: m["asset_id"] for m in clip.request["media"]}
    assert media == {"control_video": depth["id"], "start_image": done["output"]["looks"][0]}
    assert clip.model_id == "wan-vace-depth" and done["output"]["asset_id"] == steps["clip[00]"]["output"]["asset_id"]


async def test_the_picture_pass_is_made_real_opening_on_a_redrawn_frame(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, get, start

    p = await new_scene(client)
    film = (await render(client, p, frames=12))["assets"]
    opening = (await render(client, p, frames=1, times=[0]))["stills"]["beauty"][0]
    base = {"project_id": p["id"], "path": "real", "prompt": "x"}
    missing = await client.post("/pipelines", json={"recipe": "jutsu3d", "input": {**base, "model_id": "ltx-2.3-render-to-real"}})
    assert missing.status_code == 422 and "picture pass" in missing.text
    wrong = await client.post("/pipelines", json={"recipe": "jutsu3d", "input": {
        **base, "model_id": "wan-vace-depth", "render_asset_id": film["beauty"]["id"]}})
    assert wrong.status_code == 422 and "render-to-real" in wrong.text

    # billed per output megapixel: frames x 480 x 480 x the render's shape (128:72)
    est = await client.post("/pipelines/estimate", json={"recipe": "jutsu3d", "input": {
        **base, "model_id": "ltx-2.3-render-to-real", "render_asset_id": film["beauty"]["id"],
        "params": {"resolution": "480p", "num_frames": 25}}})
    assert est.status_code == 200, est.text
    assert est.json()["live_estimate_usd"] == pytest.approx(25 * 480 * 480 * 128 / 72 / 1e6 * 0.0024075, abs=0.001)

    run = await start(client, "jutsu3d", project_id=p["id"], path="real", model_id="ltx-2.3-render-to-real",
                      prompt="A misty forest path at dawn", render_asset_id=film["beauty"]["id"],
                      params={"resolution": "480p", "num_frames": 25, "frames_per_second": 12, "aspect_ratio": "1:1"},
                      stills=[{"asset_id": opening["id"], "time": 0}])
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert steps["clip[00]"]["label"].startswith("Make the picture pass real")
    async with session_scope() as s:
        clip = await s.get(Generation, uuid.UUID(steps["clip[00]"]["generation_id"]))
    media = {m["role"]: m["asset_id"] for m in clip.request["media"]}
    assert media == {"input_video": film["beauty"]["id"], "start_image": done["output"]["looks"][0]}
    assert clip.request["params"]["num_frames"] == 25
    # the scene's shape reaches the opening redraw, and the clip only gets what its model takes
    assert "aspect_ratio" not in clip.request["params"] and not clip.request.get("warnings")
    async with session_scope() as s:
        look = await s.get(Generation, uuid.UUID(steps["look[00]"]["generation_id"]))
    assert look.request["params"]["aspect_ratio"] == "1:1"


# ---------------------------------------------------------------- models and worlds


async def test_a_glb_imports_with_a_browser_thumbnail_and_bad_files_are_refused(client, tmp_path):
    glb = tmp_path / "chair.glb"
    make_glb(glb, seed=3)
    r = await client.post("/uploads", files={"file": ("chair.glb", glb.read_bytes(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    chair = r.json()
    assert chair["kind"] == "model3d" and chair["mime"] == "model/gltf-binary" and not chair["thumb_url"]
    fake = await client.post("/uploads", files={"file": ("x.glb", b"definitely not glTF", "model/gltf-binary")})
    assert fake.status_code == 415 and "GLB" in fake.text

    thumb = await client.post(f"/assets/{chair['id']}/thumbnail", files={"file": ("t.png", jpeg(120), "image/jpeg")})
    assert thumb.status_code == 200, thumb.text
    assert thumb.json()["thumb_url"]
    bad = await client.post(f"/assets/{chair['id']}/thumbnail", files={"file": ("t.gif", b"GIF89a", "image/gif")})
    assert bad.status_code == 415
    models = (await client.get("/assets", params={"kind": "model3d"})).json()["items"]
    assert chair["id"] in [a["id"] for a in models]


async def test_mock_image_to_3d_and_world_generations_keep_their_files(client, in_memory_queue):
    from tests.integration.test_generation_flow import create, drive

    p = await new_scene(client)
    still = (await render(client, p, frames=1, times=[0]))["stills"]["beauty"][0]
    out = await create(client, model_id="trellis-2", mode="image_to_3d",
                       media=[{"role": "input_image", "asset_id": still["id"]}])
    assert out["routing"]["live_reference"]["estimate_usd"] == pytest.approx(0.30)
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status.value == "succeeded", gen.error_message
    (model,) = (await client.get(f"/generations/{out['id']}")).json()["assets"]
    assert model["kind"] == "model3d" and model["mime"] == "model/gltf-binary"
    assert model["title"] == "TRELLIS.2: Picture to 3D"  # no prompt, so it's named for what made it
    assert (await client.get(model["url"])).content[:4] == b"glTF"

    out = await create(client, model_id="marble", mode="text_to_world", prompt="a stone courtyard at dusk",
                       params={"model": "marble-1.0-draft"})
    assert out["routing"]["live_reference"]["estimate_usd"] == pytest.approx(0.184)
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status.value == "succeeded", gen.error_message
    assets = (await client.get(f"/generations/{out['id']}")).json()["assets"]
    by_kind = {a["kind"]: a for a in assets}
    assert set(by_kind) == {"splat", "image", "model3d"}
    pano = by_kind["image"]
    assert pano["metadata"]["projection"] == "equirectangular" and pano["width"] == 2 * pano["height"]
    assert by_kind["model3d"]["metadata"]["part"] == "collider"
    panos = (await client.get("/assets", params={"projection": "equirectangular"})).json()["items"]
    assert pano["id"] in [a["id"] for a in panos] and model["id"] not in [a["id"] for a in panos]


async def test_rigging_keeps_the_rig_and_its_clips_and_a_scene_only_borrows_its_own_glbs(client, in_memory_queue, tmp_path):
    from tests.integration.test_generation_flow import create, drive

    glb = tmp_path / "figure.glb"
    make_glb(glb, seed=5)
    r = await client.post("/uploads", files={"file": ("figure.glb", glb.read_bytes(), "model/gltf-binary")})
    assert r.status_code == 201, r.text
    out = await create(client, model_id="meshy-rigging", mode="rig", params={"height_meters": 1.8},
                       media=[{"role": "input_model", "asset_id": r.json()["id"]}])
    assert out["routing"]["live_reference"]["estimate_usd"] == pytest.approx(0.20)
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status.value == "succeeded", gen.error_message
    assets = (await client.get(f"/generations/{out['id']}")).json()["assets"]
    assert [a["kind"] for a in assets] == ["model3d"] * 4
    rig, walk = assets[0], assets[1]
    assert rig["metadata"].get("rig") == "meshy" and "clip_source" not in rig["metadata"]
    assert walk["metadata"]["clip_source"] == "walking"

    p = await new_scene(client)
    doc = p["document"]
    doc["objects"].append({"id": "mwalker", "kind": "model", "asset_id": rig["id"],
                           "animation": {"clip": "Walking", "source_asset_id": walk["id"]}})
    ok = await client.put(f"/projects/{p['id']}", json={"document": doc, "version": p["version"]})
    assert ok.status_code == 200, ok.text
    doc["objects"][-1]["animation"]["source_asset_id"] = str(uuid.uuid4())  # not one of yours
    bad = await client.put(f"/projects/{p['id']}", json={"document": doc, "version": ok.json()["version"]})
    assert bad.status_code == 422 and "assets not found" in bad.text
