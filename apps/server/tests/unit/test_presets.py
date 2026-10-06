"""Presets: template filling and the registry lint that keeps them runnable."""

from __future__ import annotations

import copy

import pytest

from studio.config import get_settings
from studio.registry.loader import load_registry
from studio.registry.presets import PresetError, lint_presets, placeholders, render, render_prompt
from studio.registry.schema import PresetSpec

REG = load_registry(get_settings().resolve_registry_dir())


def preset(**over) -> PresetSpec:
    base = {
        "id": "t", "name": "T", "category": "look", "summary": "s", "model_id": "kling-3.0", "mode": "i2v",
        "prompt": "{subject} walks, {mood}", "inputs": [{"role": "start_image"}],
        "fields": [{"key": "subject", "label": "Who"}, {"key": "mood", "label": "Mood", "required": False}],
        "params": {"duration": 5}, "direction": {"camera_moves": [{"id": "dolly_in"}], "lighting": "golden_hour"},
    }
    return PresetSpec.model_validate({**base, **over})


def lint_one(p: PresetSpec) -> list[str]:
    reg = copy.copy(REG)
    reg.presets = {p.id: p}
    return lint_presets(reg)


def test_every_shipped_preset_is_clean():
    assert REG.presets, "registry/presets should ship some presets"
    assert lint_presets(REG) == []


def test_template_filling_tidies_optional_blanks():
    p = preset()
    assert placeholders(p.prompt) == ["subject", "mood"]
    assert render_prompt(p, {"subject": "  a  diver ", "mood": "calm"}) == "a diver walks, calm"
    assert render_prompt(p, {"subject": "a diver"}) == "a diver walks"
    with pytest.raises(PresetError, match="Who is required"):
        render_prompt(p, {"mood": "calm"})


def test_render_requires_inputs_and_keeps_only_declared_roles():
    p = preset()
    with pytest.raises(PresetError, match="add start image"):
        render(p, {"subject": "x"}, [])
    out = render(p, {"subject": "x"}, [{"role": "start_image", "asset_id": "a"}, {"role": "end_image", "asset_id": "b"}])
    assert out["media"] == [{"role": "start_image", "asset_id": "a"}]
    assert out["params"] == {"duration": 5} and out["direction"]["lighting"] == "golden_hour"


@pytest.mark.parametrize(("over", "message"), [
    ({"model_id": "nope"}, "unknown model"),
    ({"mode": "flf2v"}, "has no mode"),
    ({"prompt": "{subject} and {who}"}, "undeclared fields"),
    ({"prompt": "{subject} only"}, "never used"),
    ({"inputs": []}, "needs inputs for ['start_image']"),
    ({"inputs": [{"role": "start_image"}, {"role": "input_video"}]}, "no media slot 'input_video'"),
    ({"params": {"duration": 99}}, "duration"),
    ({"params": {"frames": 3}}, "unknown parameter"),
    ({"direction": {"camera_moves": [{"id": "moonwalk"}]}}, "unknown camera move"),
    ({"direction": {"palette": "plaid"}}, "unknown palette"),
])
def test_lint_catches_broken_presets(over, message):
    assert any(message in e for e in lint_one(preset(**over))), lint_one(preset(**over))
