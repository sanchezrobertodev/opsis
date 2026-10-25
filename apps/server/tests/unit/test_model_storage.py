from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from studio.api.deps import current_user
from studio.api.routers import model_storage as routes
from studio.config import Settings
from studio.providers import model_storage


@pytest.fixture
def configured(tmp_path):
    models = tmp_path / "original models"
    (models / "checkpoints").mkdir(parents=True)
    (models / "checkpoints/existing.safetensors").write_bytes(b"keep this model")
    config = tmp_path / "models.yaml"
    config.write_text(yaml.safe_dump({
        "custom": {"base_path": str(tmp_path / "custom"), "loras": "special"},
        "studio_models": {"base_path": str(models), "is_default": True, "checkpoints": "checkpoints"},
    }))
    return Settings(_env_file=None, comfyui_url="http://127.0.0.1:8188", comfyui_model_paths_config=config)


def test_change_preserves_files_and_custom_search_paths(configured, tmp_path):
    destination = tmp_path / "new models"
    result = model_storage.save_directory(configured, str(destination))
    config = yaml.safe_load(configured.comfyui_model_paths_config.read_text())
    assert result["directory"] == str(destination)
    assert result["free_bytes"] > 0
    assert str(tmp_path / "original models") in result["libraries"]
    assert (tmp_path / "original models/checkpoints/existing.safetensors").read_bytes() == b"keep this model"
    assert not (destination / "checkpoints/existing.safetensors").exists()
    assert (destination / "ultralytics/bbox").is_dir()
    assert config["custom"]["loras"] == "special"
    assert list(config)[-1] == "studio_models"  # loaded last, gets default priority
    assert config["studio_models"]["is_default"] is True


def test_repeat_save_does_not_duplicate_libraries(configured, tmp_path):
    path = str(tmp_path / "next")
    first = model_storage.save_directory(configured, path)
    assert model_storage.save_directory(configured, path)["libraries"] == first["libraries"]


@pytest.mark.parametrize("suffix", ["", "/"])
def test_same_directory_preserves_custom_mappings(configured, suffix):
    path = configured.comfyui_model_paths_config
    config = yaml.safe_load(path.read_text())
    previous = config["studio_models"]
    previous.update(checkpoints="custom-checkpoints\narchive", custom_node_models="custom-node-models")
    path.write_text(yaml.safe_dump(config))
    directory = previous["base_path"] + suffix

    model_storage.save_directory(configured, directory)
    saved = yaml.safe_load(path.read_text())
    preserved = [group for key, group in saved.items() if key != "studio_models"]
    assert {**previous, "is_default": False} in preserved
    assert saved["studio_models"]["checkpoints"] == "checkpoints"
    assert list(saved)[-1] == "studio_models"

    model_storage.save_directory(configured, directory)
    assert yaml.safe_load(path.read_text()) == saved


@pytest.mark.parametrize("path", ["", "relative/models", "../models", "\\\\server\\share", "//server/share", "bad\x00path"])
def test_invalid_path_leaves_configuration_intact(configured, path):
    before = configured.comfyui_model_paths_config.read_bytes()
    with pytest.raises(ValueError):
        model_storage.save_directory(configured, path)
    assert configured.comfyui_model_paths_config.read_bytes() == before


def test_existing_file_is_not_replaced_by_directory(configured, tmp_path):
    target = tmp_path / "file"
    target.write_text("important")
    with pytest.raises(OSError):
        model_storage.save_directory(configured, str(target))
    assert target.read_text() == "important"


def test_failed_atomic_save_preserves_configuration(configured, tmp_path, monkeypatch):
    before = configured.comfyui_model_paths_config.read_bytes()

    def fail(*args):
        raise PermissionError("read only")

    monkeypatch.setattr(model_storage.os, "replace", fail)
    with pytest.raises(PermissionError):
        model_storage.save_directory(configured, str(tmp_path / "new"))
    assert configured.comfyui_model_paths_config.read_bytes() == before
    assert not list(tmp_path.glob(".model-paths-*"))


def test_remote_engine_cannot_change_local_files(configured, tmp_path):
    configured.comfyui_url = "http://other-server:8188"
    with pytest.raises(ValueError, match="this server"):
        model_storage.save_directory(configured, str(tmp_path / "new"))
    assert not (tmp_path / "new").exists()


def test_drive_root_rejected(configured, tmp_path):
    with pytest.raises(ValueError, match="drive root"):
        model_storage.save_directory(configured, str(Path(tmp_path.anchor)))


@pytest.fixture
def api(configured, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: configured)
    active = model_storage.storage_info(configured)["directory"]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "system": {"argv": ["main.py", "--models-directory", active]},
    }))
    original = httpx.AsyncClient
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[current_user] = lambda: object()
    return TestClient(app)


def test_api_reports_restart_until_engine_uses_new_folder(api, tmp_path):
    before = api.get("/local-engine/model-storage").json()
    assert before["available"] and before["restart_required"] is False
    response = api.patch("/local-engine/model-storage", json={"directory": str(tmp_path / "chosen")})
    assert response.status_code == 200
    saved = response.json()
    assert saved["restart_required"] is True
    assert saved["active_directory"] == before["directory"]
    assert saved["directory"] == str(tmp_path / "chosen")


def test_api_invalid_path_returns_actionable_error(api):
    response = api.patch("/local-engine/model-storage", json={"directory": "relative"})
    assert response.status_code == 422
    assert "absolute" in response.json()["detail"]


def test_unconfigured_server_reports_unavailable(api, configured):
    configured.comfyui_model_paths_config = None
    response = api.get("/local-engine/model-storage")
    assert response.status_code == 200
    assert response.json()["available"] is False


async def test_offline_engine_does_not_claim_setting_is_active(configured, monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: configured)

    def offline(request):
        raise httpx.ConnectError("offline")

    original = httpx.AsyncClient
    transport = httpx.MockTransport(offline)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    result = await routes.status()
    assert result.available and result.restart_required is None and result.active_directory is None
