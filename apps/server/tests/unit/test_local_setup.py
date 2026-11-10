"""What scripts/setup_local.py sets up for each local model, and where it finds ComfyUI's folders and Python."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from studio.config import get_settings
from studio.providers.local_setup import Comfy, comfy_from, install_pack, local_models
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())
MODELS = {m.id: m for m in local_models(REG)}


def test_each_local_model_lists_only_the_files_its_graphs_read():
    assert [f["name"] for f in MODELS["esrgan-video"].files] == ["4x_NMKD-Siax_200k.pth"]  # not all of FLUX klein
    # the same wav2vec file sits in audio_encoders/ for ComfyUI's InfiniteTalk nodes and in wav2vec2/ for the wrapper
    wav2vec = "wav2vec2-chinese-base_fp16.safetensors"
    assert {f["folder"] for f in MODELS["infinitetalk"].files if f["name"] == wav2vec} == {"audio_encoders"}
    assert {f["folder"] for f in MODELS["infinitetalk-dub"].files if f["name"] == wav2vec} == {"wav2vec2"}
    qwen = MODELS["qwen3-tts"]
    assert qwen.files == () and [p.name for p in qwen.packs] == ["ComfyUI-Qwen-TTS"] and len(qwen.packs[0].ref) == 40
    assert MODELS["ltx-2.5"].min_vram_gb == 24 and MODELS["wan-2.2-5b"].min_vram_gb is None


def test_comfyui_says_where_its_folders_are(tmp_path):
    root = tmp_path / "ComfyUI_windows_portable"
    nodes = root / "ComfyUI" / "custom_nodes"
    stats = {"system": {"comfyui_version": "0.34.0", "python_version": "3.13.1", "embedded_python": True,
                        "argv": ["main.py", "--models-directory", str(tmp_path / "models")]},
             "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 5070 : cudaMallocAsync", "vram_total": int(11.9 * 2**30)}]}
    comfy = comfy_from(stats, {"custom_nodes": [str(nodes)], "loras": [str(tmp_path / "lib" / "loras"), "x"]})
    assert comfy.custom_nodes == nodes and comfy.models_dir == tmp_path / "models"
    assert comfy.gpu == "NVIDIA GeForce RTX 5070" and round(comfy.vram_gb) == 12
    assert comfy.paths("loras")[0] == tmp_path / "lib" / "loras"  # new files go where ComfyUI looks first
    assert comfy.paths("wav2vec2") == [tmp_path / "models" / "wav2vec2"]  # registered once its pack loads
    assert comfy.fits(MODELS["infinitetalk"]) and not comfy.fits(MODELS["wan-2.2-14b-fast"])
    assert comfy.python() is None
    exe = root / "python_embeded" / ("python.exe" if __import__("sys").platform == "win32" else "python")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert comfy.python() == exe
    stats["system"]["argv"] = ["main.py"]
    assert comfy_from(stats, {"custom_nodes": [str(nodes)]}).models_dir == root / "ComfyUI" / "models"


def test_files_and_packs_already_there_are_found(tmp_path):
    comfy = Comfy(custom_nodes=tmp_path / "custom_nodes", models_dir=tmp_path / "models")
    entry = {"folder": "vae", "name": "v.safetensors", "bytes": 4}
    assert not comfy.has_file(entry)
    (tmp_path / "models" / "vae").mkdir(parents=True)
    (tmp_path / "models" / "vae" / "v.safetensors").write_bytes(b"abc")
    assert not comfy.has_file(entry)  # a partial download isn't there yet
    (tmp_path / "models" / "vae" / "v.safetensors").write_bytes(b"abcd")
    assert comfy.has_file(entry)
    pack = MODELS["infinitetalk-dub"].packs[0]
    assert not comfy.has_pack(pack)
    (tmp_path / "custom_nodes" / "comfyui-wanvideowrapper").mkdir(parents=True)  # as ComfyUI-Manager names it
    assert comfy.has_pack(pack)


def test_a_pack_is_cloned_at_its_commit_and_never_moves_comfyuis_torch(tmp_path, monkeypatch):
    comfy = Comfy(custom_nodes=tmp_path, models_dir=tmp_path / "models")
    pack = MODELS["qwen3-tts"].packs[0]
    calls, pinned = [], []

    def run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir()
            (Path(cmd[-1]) / "requirements.txt").write_text("torch\ntransformers>=4.57.0\n")
        if "-c" in cmd and "pip" not in cmd:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"torch": "2.13.0+cu130", "transformers": "5.15.1"}))
        if "pip" in cmd:
            pinned.append(Path(cmd[cmd.index("-c") + 1]).read_text())
            if pip_fails:
                raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "")

    monkeypatch.setattr(subprocess, "run", run)
    pip_fails = True
    with pytest.raises(subprocess.CalledProcessError):
        install_pack(pack, comfy, Path("python.exe"))
    assert not comfy.has_pack(pack)  # a failed install leaves nothing in custom_nodes/, so the next run tries again
    pip_fails = False
    calls.clear()
    install_pack(pack, comfy, Path("python.exe"))
    assert calls[0][:2] == ["git", "clone"] and calls[1][-1] == pack.ref
    assert calls[-1][:4] == ["python.exe", "-m", "pip", "install"]
    assert pinned[-1] == "torch==2.13.0+cu130\ntransformers==5.15.1\n"
    assert comfy.has_pack(pack) and (tmp_path / pack.name / "requirements.txt").is_file()


def test_settings_lists_what_each_local_model_still_needs(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.api.deps import current_user, get_session
    from studio.api.routers import local_engine
    from studio.registry.routing import RoutingEnv

    async def env(_session, _reg, _user):
        return RoutingEnv(provider_mode="mock", live_allowlist=set(), has_key=lambda _p: True,
                          has_adapter=lambda _a: True, reachable=lambda _p: True,
                          blocked=lambda r, _m: "needs a GPU with 24 GB of memory" if r.id.startswith("ltx") else None)

    monkeypatch.setattr(local_engine, "routing_env", env)
    app = FastAPI()
    app.include_router(local_engine.router)
    app.dependency_overrides[current_user] = lambda: type("U", (), {"id": None})()
    app.dependency_overrides[get_session] = lambda: None
    got = {m["id"]: m for m in TestClient(app).get("/local-engine/models").json()}
    assert set(got) == set(MODELS)
    assert got["ltx-2.5"] == {"id": "ltx-2.5", "name": "LTX-2.5", "ready": False, "reason": "needs a GPU with 24 GB of memory"}
    assert got["wan-2.2-5b"]["ready"] and got["wan-2.2-5b"]["reason"] is None
