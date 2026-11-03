"""Your own models, presets, templates and ComfyUI graphs (data/registry/, data/workflows/comfy/) load alongside the
built-ins, replace a built-in with the same id, and are followed like the built-ins."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from studio.config import get_settings
from studio.providers.comfy_workflows import registry_workflow_dirs
from studio.registry import loader
from studio.registry.loader import RegistryError, RegistryHolder, load_registry

BUILTIN = get_settings().resolve_registry_dir()
WORKFLOWS = get_settings().comfyui_workflows_dir


def _copy(src: str, dest: Path, *renames: tuple[str, str]) -> None:
    """A built-in spec saved as one of yours, with ids renamed."""
    text = (BUILTIN / src).read_text(encoding="utf-8")
    for old, new in renames:
        text = text.replace(old, new)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _my_sam(user: Path) -> Path:
    path = user / "models" / "mask" / "my-sam.yaml"
    _copy("models/mask/sam-3-image.yaml", path, ("sam-3-image", "my-sam"))
    return path


def test_your_specs_load_alongside_the_built_ins(tmp_path):
    _my_sam(tmp_path)
    _copy("presets/camera/bullet-time.yaml", tmp_path / "presets" / "my-bullet-time.yaml",
          ("id: bullet-time", "id: my-bullet-time"))
    _copy("templates/change-the-voice.yaml", tmp_path / "templates" / "my-voice.yaml",
          ("id: change-the-voice", "id: my-voice"))
    reg = load_registry(BUILTIN, tmp_path)
    assert {"my-sam", "sam-3-image"} <= reg.models.keys()
    assert "my-sam@mock" in {r.id for r in reg.models["my-sam"].routes}  # free in mock mode like any model
    assert "my-bullet-time" in reg.presets and "bullet-time" in reg.presets and "my-voice" in reg.templates
    assert reg.hash != load_registry(BUILTIN).hash  # the web's catalog cache sees them
    assert load_registry(BUILTIN, tmp_path / "missing").hash == load_registry(BUILTIN).hash


def test_errors_name_your_file(tmp_path):
    bad = tmp_path / "models" / "video" / "broken.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text("id: [broken", encoding="utf-8")
    with pytest.raises(RegistryError) as exc:
        load_registry(BUILTIN, tmp_path)
    assert [e for e in exc.value.errors if e.startswith(f"{bad.as_posix()}: not valid YAML")]


def test_a_built_in_id_replaces_the_built_in_with_a_warning(tmp_path):
    mine = tmp_path / "models" / "image" / "flux.yaml"
    _copy("models/image/flux-2-klein-4b.yaml", mine, ("name: FLUX.2 klein 4B (local)", "name: My FLUX"))
    reg = load_registry(BUILTIN, tmp_path)
    assert reg.models["flux-2-klein-4b"].name == "My FLUX"
    assert f"{mine.as_posix()}: replaces the built-in model flux-2-klein-4b (models/image/flux-2-klein-4b.yaml)" \
        in reg.warnings

    shutil.copy(mine, mine.with_name("again.yaml"))  # the same id twice among your own is a mistake
    with pytest.raises(RegistryError, match="duplicate model id flux-2-klein-4b"):
        load_registry(BUILTIN, tmp_path)


def test_holder_follows_your_files(tmp_path):
    user = tmp_path / "registry"  # made after the studio started
    holder = RegistryHolder(BUILTIN, check_every=0, user_dir=user)
    assert "my-sam" not in holder.current.models

    added = _my_sam(user)
    assert "my-sam" in holder.current.models

    added.write_text("id: [broken", encoding="utf-8")
    os.utime(added, ns=(1, 1))  # a different stamp even on coarse file systems
    reg = holder.current
    assert "my-sam" in reg.models and holder.last_error  # the last good registry stays in use


def test_your_broken_file_never_stops_the_studio_starting(tmp_path):
    bad = _my_sam(tmp_path)
    bad.write_text("id: [broken", encoding="utf-8")
    holder = RegistryHolder(BUILTIN, check_every=0, user_dir=tmp_path)
    assert "sam-3-image" in holder.current.models and holder.last_error  # on the built-ins, errors in /health

    _my_sam(tmp_path)
    os.utime(bad, ns=(2, 2))
    assert "my-sam" in holder.current.models and holder.last_error is None


def test_a_comfy_route_whose_graph_is_in_your_folder(tmp_path, monkeypatch):
    workflows, user = tmp_path / "workflows", tmp_path / "registry"
    workflows.mkdir()
    monkeypatch.setattr(get_settings(), "user_workflows_dir", workflows)
    holder = RegistryHolder(BUILTIN, check_every=0, user_dir=user, workflows=(workflows, WORKFLOWS))
    assert "my-flux" not in holder.current.models

    # the model before its graph: the lint can't find the graph yet
    _copy("models/image/flux-2-klein-4b.yaml", user / "models" / "image" / "my-flux.yaml",
          ("flux-2-klein-4b", "my-flux"), ("endpoint: flux2_klein_4b", "endpoint: my_flux"))
    assert "my-flux" not in holder.current.models
    assert any("workflow template 'my_flux' not found" in e for e in holder.last_error)

    for suffix in (".json", ".map.yaml"):  # the graph arrives: the model loads without touching its YAML
        shutil.copy(WORKFLOWS / f"flux2_klein_4b{suffix}", workflows / f"my_flux{suffix}")
    assert "my-flux" in holder.current.models and holder.last_error is None


def test_your_broken_graph_never_stops_the_studio_starting(tmp_path, monkeypatch):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    monkeypatch.setattr(get_settings(), "user_workflows_dir", workflows)
    shutil.copy(WORKFLOWS / "flux2_klein_4b.json", workflows / "flux2_klein_4b.json")
    broken = workflows / "flux2_klein_4b.map.yaml"  # replaces a built-in graph's map, pointing at no node
    broken.write_text("inputs: {prompt: [{node: nope, input: text}]}\noutputs: []\n", encoding="utf-8")
    holder = RegistryHolder(BUILTIN, check_every=0, user_dir=tmp_path / "registry", workflows=(workflows, WORKFLOWS))
    monkeypatch.setattr(loader, "_holder", holder)
    reg = holder.current
    assert "flux-2-klein-4b" in reg.models and not reg.user_files  # on the built-ins, graphs included
    assert any("missing node nope" in e for e in holder.last_error)
    assert registry_workflow_dirs() == (WORKFLOWS,)  # the adapters run the built-in graph meanwhile

    shutil.copy(WORKFLOWS / "flux2_klein_4b.map.yaml", broken)
    os.utime(broken, ns=(1, 1))
    assert holder.current.user_files and holder.last_error is None
    assert registry_workflow_dirs() == (workflows, WORKFLOWS)


async def test_open_pages_hear_of_your_first_file(tmp_path, monkeypatch):
    from studio.api import main

    user = tmp_path / "registry"  # not there when the API starts
    holder = RegistryHolder(BUILTIN, check_every=0.01, user_dir=user)
    assert "my-sam" not in holder.current.models
    events: list[dict] = []
    monkeypatch.setattr(main, "get_registry_holder", lambda: holder)
    monkeypatch.setattr(main.hub, "publish", events.append)
    watcher = asyncio.create_task(main._watch_registry())
    try:
        await asyncio.sleep(0.05)
        assert events == []  # nothing changed, nothing said
        _my_sam(user)
        deadline = time.monotonic() + 15
        while not events and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        watcher.cancel()
    assert [e["type"] for e in events] == ["system.registry_reloaded"]
    assert events[0]["payload"]["hash"] == holder.current.hash and "my-sam" in holder.current.models
