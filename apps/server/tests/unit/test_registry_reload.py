"""The registry follows its YAML in every process, so the worker never misses a model the API accepts (AMS-10)."""

from __future__ import annotations

import os
import shutil

from studio.config import get_settings
from studio.registry.loader import RegistryHolder


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
