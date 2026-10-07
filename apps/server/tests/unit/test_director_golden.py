"""Golden snapshots: one fully directed shot compiled for every prompt dialect, plus native camera params.

A changed phrase or ordering shows up as a diff in tests/unit/golden/director/. When the change is intended,
regenerate with `UPDATE_GOLDEN=1 uv run pytest apps/server/tests/unit/test_director_golden.py` and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from studio.config import get_settings
from studio.director.compiler import ShotDirection, compile_prompt, unknown_ids
from studio.director.library import get_library
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())
LIB = get_library()
GOLDEN = Path(__file__).parent / "golden" / "director"

PROMPT = "@nova climbs the lighthouse stairs as the storm hits"
NAMES = {"nova": "Nova (@Image1)"}  # what @mention resolution makes of the cast member
FULL = ShotDirection(
    subject="Nova",
    camera_moves=[{"id": "crane_up", "speed": "slow"}, {"id": "orbit_360"}, {"id": "dolly_zoom_in"}],
    shot_size="medium_wide", angle="low", lens="anamorphic", focal_length="normal_35", aperture="f2",
    camera_body="film_35", era="eighties", lighting="volumetric",
    lights=[{"source": "spotlight", "position": "left", "color": "warm_tungsten", "quality": "hard"},
            {"source": "moon", "position": "back", "color": "moon_blue", "intensity": "dim"}],
    palette="cold_steel", genre="thriller", pacing="dynamic",
    cast_emotions=[{"who": "@nova", "emotion": "terror"}, {"who": "the keeper", "emotion": "determination"}],
)
# one model per dialect; wan-2.2-fun-camera also turns the first camera move into its native control
CASES = ["veo-3.1", "kling-3.0", "wan-2.2-5b", "seedance-2.5", "runway-gen-4.5", "luma-ray-3.2", "nano-banana-2",
         "flux-2-klein-4b", "wan-2.2-fun-camera"]


def render(model_id: str, direction: ShotDirection) -> str:
    spec = REG.model(model_id)
    assert spec is not None
    c = compile_prompt(spec, PROMPT.replace("@nova", NAMES["nova"]), direction, names=NAMES)
    return "\n".join([
        f"# {model_id} (dialect {c.dialect}, {len(c.prompt)}/{spec.prompt.max_chars} characters)",
        c.prompt,
        "--- warnings",
        *c.warnings,
        "--- native params",
        json.dumps(c.native_params, sort_keys=True),
        "",
    ])


@pytest.mark.parametrize("model_id", CASES)
def test_full_direction_matches_golden(model_id):
    direction = FULL
    if model_id == "wan-2.2-fun-camera":  # its native control takes the first move
        direction = ShotDirection.model_validate(
            {**FULL.model_dump(), "camera_moves": [{"id": "dolly_in"}, {"id": "pan_left"}]})
    actual = render(model_id, direction)
    path = GOLDEN / f"{model_id}.txt"
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        if new:
            pytest.fail(f"wrote a new golden file {path.name}; review it and run again")
    assert actual == path.read_text(encoding="utf-8"), f"{model_id} compiled differently; see {path}"


def test_every_dialect_in_the_registry_is_covered():
    covered = {REG.model(m).prompt.dialect for m in CASES}  # type: ignore[union-attr]
    used = {s.prompt.dialect for s in REG.models.values()
            if s.output_type in ("video", "image") and any("t2v" in m.ops or "t2i" in m.ops or "i2v" in m.ops
                                                         for m in s.modes)}
    assert used <= covered, f"add a golden case for {sorted(used - covered)}"


# ---------------------------------------------------------------- the vocabulary


def test_vocabulary_is_cinema_studio_sized():
    assert len(LIB.palettes) >= 50
    assert len(LIB.camera_bodies) >= 15 and len(LIB.focal_lengths) >= 10 and len(LIB.apertures) >= 8
    assert {e.label for e in LIB.eras} >= {"1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"}
    assert [p.label for p in LIB.pacing] == ["Calm", "Dynamic", "Chaotic", "Single shot"]
    assert all(len(p.swatch) == 3 for p in LIB.palettes)
    assert all(len(c.swatch) == 1 for c in LIB.light_colors)


def test_the_emotion_wheel_is_complete():
    cells = {(e.family, e.level) for e in LIB.emotions}
    families = {e.family for e in LIB.emotions}
    assert len(families) == 8 and cells == {(f, n) for f in families for n in (1, 2, 3)}
    assert all("{subject}" in e.prompt for e in LIB.emotions)


def test_focal_lengths_saved_as_lenses_move_over():
    d = ShotDirection.model_validate({"lens": "portrait_85"})
    assert (d.lens, d.focal_length) == (None, "portrait_85")
    d = ShotDirection.model_validate({"lens": "anamorphic", "focal_length": "tele_135"})
    assert (d.lens, d.focal_length) == ("anamorphic", "tele_135")


def test_cast_emotions_follow_the_prompt():
    veo = REG.model("veo-3.1")
    d = ShotDirection(cast_emotions=[{"who": "@nova", "emotion": "joy"}, {"who": "@kai", "emotion": "fear"}])
    c = compile_prompt(veo, "Nova (@Image1) waves", d, names={"nova": "Nova (@Image1)"})
    assert "Nova (@Image1) radiates joy." in c.prompt and "kai" not in c.prompt.lower()
    assert c.warnings == ["@kai isn't in this shot, so their emotion was left out"]
    # without mention resolution (a preview), mentions stay as written
    assert "@kai is gripped by fear" in compile_prompt(veo, "x", d).prompt
    with pytest.raises(ValueError):
        ShotDirection(cast_emotions=[{"who": "@nova", "emotion": "joy"}, {"who": "@Nova", "emotion": "fear"}])


def test_lights_read_as_key_then_second():
    d = ShotDirection(lights=[{"source": "window", "position": "right"}, {"source": "practical", "color": "amber",
                                                                        "position": "top", "intensity": "bright"}])
    p = compile_prompt(REG.model("kling-3.0"), "a kitchen", d).prompt  # type: ignore[arg-type]
    assert p == ("a kitchen, key light: soft window light from the right, "
                 "second light: bright soft amber practical lamp from overhead")


def test_unknown_ids_are_reported_for_lint_and_ignored_when_compiling():
    d = ShotDirection(focal_length="9000mm", lights=[{"source": "laser", "position": "inside"}],
                      cast_emotions=[{"who": "x", "emotion": "smug"}])
    assert unknown_ids(d) == ["unknown focal length '9000mm'", "unknown light source 'laser'",
                              "unknown light position 'inside'", "unknown emotion 'smug'"]
    c = compile_prompt(REG.model("veo-3.1"), "x", d)  # type: ignore[arg-type]
    assert c.prompt == "X." and len(c.warnings) == 3


def test_a_look_can_read_differently_per_dialect():
    lib = LIB.model_copy(deep=True)
    era = lib.look("eras", "eighties")
    assert era is not None
    era.dialects = {"kling": "1980s VHS-era look"}
    d = ShotDirection(era="eighties")
    assert compile_prompt(REG.model("kling-3.0"), "x", d, lib).prompt == "x, 1980s VHS-era look"  # type: ignore[arg-type]
    assert "neon accents" in compile_prompt(REG.model("veo-3.1"), "x", d, lib).prompt  # type: ignore[arg-type]
