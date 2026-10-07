"""Virtual models: Cinema Studio resolves to its engine's base model before anything else sees the request."""

from __future__ import annotations

import re

import pytest

from studio.config import get_settings
from studio.registry.lint import _lint_virtual
from studio.registry.loader import load_registry
from studio.registry.validate import RequestError
from studio.registry.virtual import resolve

REG = load_registry(get_settings().resolve_registry_dir())
CINEMA = REG.models["cinema-studio"]


def test_default_engine_is_seedance_and_director_params_become_direction():
    r = resolve(REG, CINEMA, "t2v", {"duration": 5, "resolution": "720p", "aspect_ratio": "21:9",
                                     "generate_audio": True, "era": "eighties", "lens": "anamorphic"}, None)
    assert (r.spec.id, r.mode) == ("seedance-2.5", "t2v")
    assert r.params == {"duration": 5, "resolution": "720p", "aspect_ratio": "21:9", "generate_audio": True}
    assert r.direction == {"era": "eighties", "lens": "anamorphic"}
    assert r.warnings == []
    assert r.via == {"model_id": "cinema-studio", "name": "Cinema Studio", "mode": "t2v", "engine": "seedance",
                     "engine_label": "Seedance 2.5"}


def test_unset_params_take_the_studios_defaults_not_the_engines():
    veo = resolve(REG, CINEMA, "t2v", {"engine": "veo"}, None)
    assert veo.params == {"duration": 6, "resolution": "720p", "aspect_ratio": "16:9", "generate_audio": True}
    assert veo.warnings == []  # nobody asked for 5 s, so rounding it isn't news
    kling = resolve(REG, CINEMA, "i2v", {"engine": "kling"}, None)
    assert kling.params == {"duration": 5, "resolution": "720p", "generate_audio": True}  # Kling's own is silent


def test_the_direction_wins_over_a_director_param():
    r = resolve(REG, CINEMA, "t2v", {"era": "eighties", "palette": "sepia"}, {"era": "nineties"})
    assert r.direction == {"era": "nineties", "palette": "sepia"}


def test_other_engines_fit_what_they_can_and_say_what_they_dropped():
    r = resolve(REG, CINEMA, "t2v", {"engine": "veo", "duration": 5, "resolution": "480p", "aspect_ratio": "21:9"},
                None)
    assert (r.spec.id, r.mode) == ("veo-3.1", "t2v")
    assert r.params == {"duration": 6, "generate_audio": True}
    assert r.warnings == ["Veo 3.1 makes 6 s clips, not 5 s", "Veo 3.1 has no 480p resolution; using its default",
                          "Veo 3.1 has no 21:9 aspect; using its default"]
    edit = resolve(REG, CINEMA, "video_edit", {"engine": "kling", "resolution": "720p"}, None)
    assert (edit.spec.id, edit.mode, edit.params, edit.warnings) == ("kling-o3-edit", "edit", {}, [])


def test_an_engine_without_the_mode_points_at_one_that_has_it():
    with pytest.raises(RequestError, match=re.escape("Kling 3.0 can't do omni reference in Cinema Studio; "
                                                     "Seedance 2.5 or Veo 3.1 can")):
        resolve(REG, CINEMA, "omni_reference", {"engine": "kling"}, None)
    with pytest.raises(RequestError, match="no engine 'sora'"):
        resolve(REG, CINEMA, "t2v", {"engine": "sora"}, None)


def test_ordinary_models_pass_through():
    spec = REG.models["kling-3.0"]
    r = resolve(REG, spec, "t2v", {"duration": 7}, {"era": "eighties"})
    assert (r.spec, r.mode, r.params, r.direction, r.via) == (spec, "t2v", {"duration": 7}, {"era": "eighties"}, None)


def test_director_params_list_the_vocabulary():
    palette = CINEMA.param("palette")
    assert len(palette.options) >= 50 and palette.labels["teal_orange"] == "Teal and orange"  # type: ignore[union-attr]


def test_lint_checks_engines_and_params():
    assert _lint_virtual(REG, CINEMA, "m") == []
    broken = CINEMA.model_copy(deep=True)
    assert broken.virtual is not None
    broken.virtual.engines[1].modes["t2v"] = "kling-3.0/nope"
    broken.virtual.engines[2].modes["t2v"] = "runway-gen-4.5/t2v"
    broken.virtual.director.append("subject")
    broken.parameters = [p for p in broken.parameters if p.name != "era"]
    assert _lint_virtual(REG, broken, "m") == [
        "m: engine kling mode t2v -> 'kling-3.0/nope' doesn't exist",
        "m: engine veo uses runway-gen-4.5, which `wraps` doesn't list",
        "m: director param era must be `type: enum, director: eras`",
        "m: director param 'subject' is not a shot direction field",
    ]
