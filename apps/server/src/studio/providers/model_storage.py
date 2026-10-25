"""Manage ComfyUI's default model folder without moving existing libraries."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import yaml

from studio.config import Settings

DEFAULT_GROUP = "studio_models"
MODEL_FOLDERS = {
    name: name for name in (
        "checkpoints", "configs", "loras", "vae", "clip_vision", "style_models", "embeddings",
        "diffusers", "vae_approx", "gligen", "upscale_models", "latent_upscale_models", "hypernetworks",
        "photomaker", "classifiers", "model_patches", "audio_encoders", "background_removal",
        "frame_interpolation", "geometry_estimation", "optical_flow", "detection",
    )
} | {
    "text_encoders": "text_encoders\nclip",
    "diffusion_models": "diffusion_models\nunet",
    "controlnet": "controlnet\nt2i_adapter",
    "ultralytics_bbox": "ultralytics/bbox",
    "ultralytics_segm": "ultralytics/segm",
}
_write_lock = Lock()


def config_path(settings: Settings) -> Path:
    if not settings.comfyui_model_paths_config:
        raise ValueError("Model storage is not connected to this studio. Set COMFYUI_MODEL_PATHS_CONFIG on the server.")
    if urlparse(settings.comfyui_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Model storage can be changed only for ComfyUI running on this server.")
    return settings.comfyui_model_paths_config


def read_config(settings: Settings) -> dict:
    config = yaml.safe_load(config_path(settings).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or not isinstance(config.get(DEFAULT_GROUP), dict):
        raise ValueError("Connect the ComfyUI launcher to the studio_models configuration first.")
    directory = config[DEFAULT_GROUP].get("base_path")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise ValueError("The configured model folder must be an absolute path.")
    return config


def storage_info(settings: Settings) -> dict:
    config = read_config(settings)
    directory = Path(config[DEFAULT_GROUP]["base_path"]).resolve()
    libraries = []
    for group in config.values():
        if isinstance(group, dict) and isinstance(group.get("base_path"), str):
            base = Path(group["base_path"])
            if not base.is_absolute():
                base = config_path(settings).parent / base
            path = str(base.resolve())
            if path != str(directory) and path not in libraries:
                libraries.append(path)
    return {"directory": str(directory), "libraries": libraries,
            "free_bytes": shutil.disk_usage(directory).free if directory.is_dir() else None}


def save_directory(settings: Settings, value: str) -> dict:
    raw = value.strip()
    if not raw or "\x00" in raw or raw.startswith(("\\\\", "//")):
        raise ValueError("Enter an absolute folder path on this server, such as G:\\AI\\Models\\ComfyUI.")
    directory = Path(raw).expanduser()
    if not directory.is_absolute() or directory == Path(directory.anchor):
        raise ValueError("Choose an absolute folder path, not a drive root or a relative path.")
    directory = directory.resolve()
    if not directory.anchor or not Path(directory.anchor).is_dir():
        raise ValueError("That drive is not available on this server.")
    with _write_lock:
        config = read_config(settings)
        previous = config.pop(DEFAULT_GROUP)
        old_path = Path(previous["base_path"]).resolve()
        if old_path != directory:
            # Keep each previous library searchable; never move or delete its contents.
            key = "studio_library_" + str(len(config))
            while key in config:
                key += "_old"
            config[key] = {**previous, "is_default": False}
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as probe:
            probe.write(b"model storage write check")
            probe.flush()
        for paths in MODEL_FOLDERS.values():
            for relative in paths.splitlines():
                (directory / relative).mkdir(parents=True, exist_ok=True)
        config[DEFAULT_GROUP] = {"base_path": str(directory), "is_default": True, **MODEL_FOLDERS}
        path = config_path(settings)
        # A failed save leaves the previous configuration intact.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".model-paths-", suffix=".yaml", delete=False) as output:
                temporary = Path(output.name)
                yaml.safe_dump(config, output, sort_keys=False, allow_unicode=True)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return storage_info(settings)
