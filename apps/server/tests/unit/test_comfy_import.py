from __future__ import annotations

import json

import pytest
import yaml

from studio.config import get_settings
from studio.providers import comfy_import
from studio.providers.comfy_workflows import load_template
from studio.registry.lint import _check_workflows
from studio.registry.schema import ModelSpec

WORKFLOWS = get_settings().comfyui_workflows_dir


def _graph(name: str) -> dict:
    return comfy_import.read_graph((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))


def _hand_map(name: str) -> dict:
    spec = yaml.safe_load((WORKFLOWS / f"{name}.map.yaml").read_text(encoding="utf-8"))
    return {k: [{"node": t["node"], "input": t["input"]} | ({"mode": t["mode"]} if "mode" in t else {}) for t in v]
            for k, v in spec["inputs"].items()}


@pytest.mark.parametrize("name", ["wan22_ti2v_5b", "flux2_klein_4b"])
def test_guesses_match_the_hand_written_maps(name):
    draft = comfy_import.analyze(_graph(name))
    assert draft.inputs == _hand_map(name)


def test_first_and_last_frames_and_repeated_sizes():
    draft = comfy_import.analyze(_graph("runpod_ltx25_flf2v"))
    assert [s["role"] for s in draft.slots] == ["start_image", "end_image"]
    assert {t["node"] for t in draft.inputs["width"]} == {"9", "31", "36"}  # the latent and both frame resizes
    assert {t["input"] for t in draft.inputs["length"]} == {"length", "frames_number"}  # video and audio latents


def test_mask_clip_gets_the_mask_role():
    draft = comfy_import.analyze(_graph("runpod_wan21_vace_edit"))
    assert {s["role"]: s["kinds"] for s in draft.slots} == {
        "input_video": ["video"], "mask": ["mask_video"], "image_references": ["image"]}


def test_editor_format_is_refused_with_the_fix():
    with pytest.raises(comfy_import.NotAWorkflow, match="Export \\(API\\)"):
        comfy_import.read_graph(json.dumps({"nodes": [], "links": [], "version": 0.4}))


def test_drafted_model_loads_and_its_route_fits_the_map(tmp_path, monkeypatch):
    draft = comfy_import.analyze(_graph("wan22_ti2v_5b"))
    (tmp_path / "mine.json").write_text(json.dumps(draft.graph), encoding="utf-8")
    (tmp_path / "mine.map.yaml").write_text(comfy_import.map_yaml(draft, "Mine"), encoding="utf-8")
    spec = ModelSpec.model_validate(yaml.safe_load(comfy_import.model_yaml(draft, "mine", "Mine", "mine", 16)))
    assert spec.modes[0].ops == ["i2v"] and spec.routes[0].min_vram_gb == 16
    assert load_template(tmp_path, "mine").map.outputs[0].kind == "video"
    monkeypatch.setattr(get_settings(), "comfyui_workflows_dir", tmp_path)
    assert _check_workflows(spec, spec.routes[0], "mine") == []
