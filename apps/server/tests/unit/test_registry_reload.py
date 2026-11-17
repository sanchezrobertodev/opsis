"""The registry follows its YAML in every process, so the worker never misses a model the API accepts."""

from __future__ import annotations

import os
import shutil

import pytest

from studio.config import get_settings
from studio.registry import loader
from studio.registry.loader import RegistryError, RegistryHolder, load_registry


def test_holder_picks_up_added_and_broken_files(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(get_settings().resolve_registry_dir(), root)
    holder = RegistryHolder(root, check_every=0)
    assert "sam-3-image" in holder.current.models and "sam-3-copy" not in holder.current.models

    src = (root / "models" / "mask" / "sam-3-image.yaml").read_text(encoding="utf-8")
    added = root / "models" / "mask" / "sam-3-copy.yaml"
    added.write_text(src.replace("id: sam-3-image", "id: sam-3-copy").replace("sam-3-image@fal", "sam-3-copy@fal"),
                     encoding="utf-8")
    assert "sam-3-copy" in holder.current.models

    added.write_text("id: [broken", encoding="utf-8")
    os.utime(added, ns=(1, 1))  # a different stamp even on coarse file systems
    reg = holder.current
    assert "sam-3-copy" in reg.models and holder.last_error  # the last good registry stays in use


def test_unchanged_files_are_not_reloaded(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(get_settings().resolve_registry_dir(), root)
    holder = RegistryHolder(root, check_every=0)
    first = holder.current
    assert holder.current is first


def test_a_broken_style_file_is_rejected_like_a_broken_model(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(get_settings().resolve_registry_dir(), root)
    holder = RegistryHolder(root, check_every=0)
    good = holder.current
    style = root / "styles" / "painting" / "watercolor-chronicle.yaml"
    style.write_text(style.read_text(encoding="utf-8").replace("category: painting", "category: retro"),
                     encoding="utf-8")
    with pytest.raises(RegistryError, match=r"director library: styles/painting/watercolor-chronicle\.yaml: "
                                            r"category 'retro' must be its folder's name"):
        load_registry(root)
    assert holder.current is good  # the last good registry stays in use
    assert holder.last_error and "watercolor-chronicle.yaml" in holder.last_error[-1]


def test_wording_for_a_dialect_no_model_uses_fails_the_load(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(get_settings().resolve_registry_dir(), root)
    style = root / "styles" / "painting" / "watercolor-chronicle.yaml"
    style.write_text(style.read_text(encoding="utf-8") + "dialects: {klingg: washes of colour}\n", encoding="utf-8")
    with pytest.raises(RegistryError, match=r"director styles watercolor-chronicle: dialect 'klingg' isn't one any "
                                            r"model's prompt uses"):
        load_registry(root)


def test_an_unexpected_reload_failure_keeps_the_last_good_registry(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    shutil.copytree(get_settings().resolve_registry_dir(), root)
    holder = RegistryHolder(root, check_every=0)
    good = holder.current

    def gone(*_args):
        raise FileNotFoundError("a file went mid-read")

    monkeypatch.setattr(loader, "load_registry", gone)
    assert holder.reload() is False
    assert holder.current is good
    assert holder.last_error == ["registry reload failed: FileNotFoundError('a file went mid-read')"]
