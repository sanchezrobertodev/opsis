"""Director: vocabulary integrity and how each dialect compiles a shot."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.config import get_settings
from studio.director.compiler import MAX_MOVES, ShotDirection, compile_prompt
from studio.director.library import LOOK_SECTIONS, get_library
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())
LIB = get_library()
SHOT = ShotDirection(subject="the keeper", camera_moves=[{"id": "crane_up", "speed": "slow"}, {"id": "orbit_360"}],
                     shot_size="wide", lens="anamorphic", lighting="blue_hour", palette="teal_orange", genre="drama")
PROMPT = "A lighthouse keeper climbs a spiral staircase at night"


def test_library_has_the_full_move_set_and_sane_entries():
    assert len(LIB.moves) == 65
    assert {m.category for m in LIB.moves} >= {"push", "orbit", "vertical", "handheld", "vehicle", "time"}
    for m in LIB.moves:
        assert m.label and not m.label[0].islower()  # sentence case ("360 orbit" starts with a digit)
        assert "{subject}" in m.prompt or m.id in {"static", "general", "pan_left", "pan_right", "tilt_up", "tilt_down",
                                                   "whip_pan", "handheld", "wiggle", "road_rush", "buckle_up",
                                                   "hyperlapse", "timelapse_landscape", "low_shutter", "dutch_angle"}
    for section in LOOK_SECTIONS:
        assert getattr(LIB, section), f"{section} is empty"


def test_empty_direction_leaves_the_prompt_alone():
    assert compile_prompt(REG.model("veo-3.1"), "  hello  ", ShotDirection()).prompt == "hello"
    assert compile_prompt(REG.model("veo-3.1"), "hello", None).prompt == "hello"


def test_veo_uses_sentences_in_order():
    p = compile_prompt(REG.model("veo-3.1"), PROMPT, SHOT).prompt
    assert p.startswith(f"{PROMPT}. Wide shot.")
    assert "crane shot rising up and away from the keeper, slow and smooth, then the camera orbits" in p
    assert p.index("Wide shot") < p.index("crane") < p.index("Anamorphic") < p.index("blue-hour") < p.index("drama")


def test_kling_and_wan_use_comma_cues():
    for model in ("kling-3.0", "wan-2.2-5b"):
        p = compile_prompt(REG.model(model), PROMPT, SHOT).prompt
        assert p.startswith(f"{PROMPT}, wide shot, camera: crane shot")
        assert "." not in p.replace("oval bokeh", "")  # no sentence breaks


def test_seedance_uses_labelled_lines():
    p = compile_prompt(REG.model("seedance-2.5"), PROMPT, SHOT).prompt
    assert p.splitlines()[0] == f"{PROMPT}."
    assert any(line.startswith("Camera: crane shot") for line in p.splitlines())
    assert any(line.startswith("Mood:") for line in p.splitlines())


def test_images_drop_camera_moves_with_a_warning():
    c = compile_prompt(REG.model("nano-banana-2"), PROMPT, SHOT)
    assert "crane" not in c.prompt and "Anamorphic" in c.prompt
    assert any("still images" in w for w in c.warnings)


def test_unknown_ids_warn_instead_of_failing():
    c = compile_prompt(REG.model("veo-3.1"), PROMPT, ShotDirection(camera_moves=[{"id": "teleport"}], lens="nope"))
    assert c.prompt == f"{PROMPT}."
    assert len(c.warnings) == 2


def test_speed_and_subject_defaults():
    c = compile_prompt(REG.model("kling-3.0"), "x", ShotDirection(camera_moves=[{"id": "dolly_in", "speed": "fast"}]))
    assert "dolly in toward the subject, fast" in c.prompt


def test_too_many_moves_rejected():
    with pytest.raises(ValidationError):
        ShotDirection(camera_moves=[{"id": "dolly_in"}] * (MAX_MOVES + 1))


def test_long_prompts_shed_soft_cues_first():
    spec = REG.model("kling-3.0").model_copy(deep=True)
    spec.prompt.max_chars = 260
    c = compile_prompt(spec, PROMPT, SHOT)
    assert "crane" in c.prompt  # camera moves are never dropped
    assert "drama" not in c.prompt  # mood went first
    assert any("dropped mood" in w for w in c.warnings)


def test_let_the_model_decide_adds_nothing():
    c = compile_prompt(REG.model("veo-3.1"), "x", ShotDirection(camera_moves=[{"id": "general"}]))
    assert c.prompt == "X." or c.prompt == "x."
