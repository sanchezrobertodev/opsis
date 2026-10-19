"""Presets: template filling and the registry lint that keeps them runnable."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from studio.config import get_settings
from studio.registry.loader import load_registry
from studio.registry.presets import (
    PresetError,
    lint_presets,
    lint_templates,
    placeholders,
    render,
    render_prompt,
    render_template,
)
from studio.registry.schema import PresetSpec, TemplateSpec

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


# ---------------------------------------------------------------- templates (recipe runs)


def template(**over) -> TemplateSpec:
    base = {
        "id": "t", "name": "Talk", "summary": "s", "recipe": "talking_shot",
        "inputs": [{"key": "image_asset_id", "kind": "image", "label": "A portrait"}],
        "fields": [{"key": "script", "label": "Script"}],
        "input": {"script": "Hello, {script}", "models": [{"model_id": "kling-avatar-2"}]},
    }
    return TemplateSpec.model_validate({**base, **over})


def lint_template(t: TemplateSpec) -> list[str]:
    reg = copy.copy(REG)
    reg.templates = {t.id: t}
    return lint_templates(reg)


def test_every_shipped_template_is_clean():
    assert REG.templates, "registry/templates should ship some templates"
    assert lint_templates(REG) == []


def test_template_render_fills_blanks_and_media():
    t = template()
    with pytest.raises(PresetError, match="add a portrait"):
        render_template(t, {"script": "world"}, {})
    out = render_template(t, {"script": " world "}, {"image_asset_id": "a1"})
    assert out == {"recipe": "talking_shot", "title": "Talk", "input": {
        "script": "Hello, world", "models": [{"model_id": "kling-avatar-2"}], "image_asset_id": "a1"}}


@pytest.mark.parametrize(("over", "message"), [
    ({"recipe": "nope"}, "unknown recipe"),
    ({"input": {"script": "{who}", "models": [{"model_id": "kling-avatar-2"}]}}, "undeclared fields"),
    ({"input": {"script": "hi", "models": [{"model_id": "kling-avatar-2"}]}}, "never used"),
    ({"input": {"script": "{script}", "models": []}}, "models"),
    ({"input": {"script": "{script}", "models": [{"model_id": "veo-3.1"}]}}, "can't make a picture speak"),
    ({"input": {"script": "{script}", "image_asset_id": "x", "models": [{"model_id": "kling-avatar-2"}]}},
     "overwrite fixed input"),
])
def test_lint_catches_broken_templates(over, message):
    assert any(message in e for e in lint_template(template(**over))), lint_template(template(**over))


def test_every_card_has_a_cover():
    """The Templates page shows apps/web/public/covers/<id>.webp on each card (a drawn stand-in if it's missing)."""
    covers = Path(__file__).resolve().parents[4] / "apps" / "web" / "public" / "covers"
    missing = [i for i in [*REG.presets, *REG.templates] if not (covers / f"{i}.webp").exists()]
    assert not missing, f"add covers for {missing}"


def test_optional_inputs_and_saved_characters_in_templates():
    t = template(inputs=[{"key": "image_asset_id", "kind": "image", "label": "A portrait", "alternative": True}])
    with pytest.raises(PresetError, match="add a portrait"):  # alternatives: one of them is needed
        render_template(t, {"script": "x"}, {})
    star = TemplateSpec.model_validate({
        "id": "v", "name": "V", "summary": "s", "recipe": "vlog", "fields": [{"key": "idea", "label": "Idea"}],
        "input": {"idea": "{idea}"}, "inputs": [
            {"key": "character_image", "kind": "image", "label": "A picture", "alternative": True},
            {"key": "character", "kind": "character", "label": "Or a saved character", "alternative": True}]})
    assert lint_template(star) == []
    assert render_template(star, {"idea": "x"}, {"character": "maya"})["input"] == {"idea": "x", "character": "maya"}
    with pytest.raises(PresetError, match="add a picture or a saved character"):
        render_template(star, {"idea": "x"}, {})
