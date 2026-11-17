"""Looks (`@handle:look`), casts (saved groups of characters) and the grid sheets: the parts that need no database.
The routes and mention resolution are in tests/integration/test_looks_and_casts.py."""

from __future__ import annotations

import re
import uuid

import pytest
from pydantic import ValidationError

from studio.cinema.document import Scene
from studio.cinema.planner import PlannedEmotion, PlannedScene, PlannedShot, ShotPlan, _unmention, to_scenes
from studio.director.compiler import handle_of
from studio.director.elements import LOOK_SLUG, MENTION_LOOK, UNATTACHED_VIEWS, mentions, per_member, worn
from studio.director.library import get_library
from studio.pipelines.recipes import get_recipe
from studio.pipelines.recipes.character_look import CharacterLookInput, worded
from studio.pipelines.recipes.character_photos import ANGLES
from studio.pipelines.recipes.character_sheet import (
    GRIDS,
    POSES,
    SHEET,
    CharacterSheetInput,
    cells,
    sheet_prompt,
)
from studio.registry.loader import get_registry


def found(text: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in MENTION_LOOK.finditer(text)]


def test_a_look_is_a_colon_right_before_a_letter_or_digit():
    assert found("@maya:winter walks with @kai") == [("maya", "winter"), ("kai", None)]
    assert found("@Maya:Red-Coat-2 at noon") == [("Maya", "Red-Coat-2")]
    # a mention that ends a sentence or opens a line of dialogue stays a plain one
    assert found("@maya: she says hi") == [("maya", None)]
    assert found("In the style of @ink-wash.") == [("ink-wash", None)]
    assert found("@maya:") == [("maya", None)]
    assert found("email me@x.com") == []
    # everything but mention resolution reads the handles alone
    assert mentions("@maya:winter meets @kai: hello, then @Maya:summer") == ["maya", "kai"]
    # a rewording must keep each character and the look it wears (Claude's reword, Enhance's warnings); a plain
    # mention of one wearing a look elsewhere in the prompt wears it too
    assert worn("@maya:winter meets @Kai: hello, then @maya and @Maya:Summer") == ["maya:winter", "kai"]
    assert worn("@maya waves, then @maya:winter smiles") == worn("@maya:winter waves and smiles")
    assert worn("@maya waves") != worn("@maya:winter waves")


@pytest.mark.parametrize(
    ("slug", "valid"),
    [
        ("winter", True),
        ("red-coat-2", True),
        ("a", True),
        ("x" * 24, True),
        ("x" * 25, False),
        ("-coat", False),
        ("Winter", False),
        ("red_coat", False),
        ("", False),
    ],
)
def test_look_handles(slug, valid):
    assert bool(re.match(LOOK_SLUG, slug)) is valid


@pytest.mark.parametrize(
    ("room", "members", "each"),
    [(3, 2, 1), (10, 3, 2), (2, 4, 1), (16, 8, 2), (14, 8, 1), (9, 2, 2), (0, 3, 1)],
)
def test_each_cast_member_gets_an_even_share_of_two_at_most(room, members, each):
    # at least one: with too little room the last ones get none, and are described in words
    assert per_member(room, members) == each


def test_a_speaker_is_the_handle_without_its_look():
    assert handle_of("@maya:winter") == "maya"
    assert handle_of(" @Kai ") == "kai"
    assert handle_of("@maya") == "maya"
    assert handle_of("Maya") is None


def test_a_planned_handle_nobody_has_reads_as_a_word_without_its_look():
    scene = Scene.model_validate(
        {
            "heading": "x",
            "shots": [
                {
                    "action": "@keeper:snow climbs; @ghost:winter waits.",
                    "lines": [{"who": "@ghost:winter", "text": "Boo."}],
                    "direction": {"cast_emotions": [{"who": "@keeper:snow", "emotion": "fear"}]},
                }
            ],
        }
    )
    shot = _unmention(scene, {"keeper"}).shots[0]
    assert shot.action == "@keeper:snow climbs; ghost waits." and shot.lines[0].who == "ghost"
    assert [e.who for e in shot.direction.cast_emotions] == ["@keeper:snow"]


def test_a_planned_shot_gives_each_person_one_feeling_whichever_look_it_names():
    """A look is the same person: the first feeling stays, under the handle without its look."""
    shot = PlannedShot(
        action="@maya:winter watches young @maya play",
        emotions=[
            PlannedEmotion(who="@maya:winter", emotion="fear"),
            PlannedEmotion(who="Maya", emotion="joy"),
        ],
    )
    scenes, _ = to_scenes(
        ShotPlan(scenes=[PlannedScene(heading="x", summary="y", shots=[shot])]), get_library(), 5
    )
    assert [(e.who, e.emotion) for e in scenes[0].shots[0].direction.cast_emotions] == [("@maya", "fear")]


def test_a_turnaround_sheet_plans_what_it_always_did():
    """Finished runs' steps must not change: the turnaround keeps its prompt, view and place."""
    reg, eid = get_registry(), uuid.uuid4()
    recipe = get_recipe("character_sheet")
    inp = CharacterSheetInput(element_id=eid, slug="griffin")
    assert inp.sheet == "turnaround" and sheet_prompt("turnaround", "griffin") == SHEET.format(slug="griffin")
    draw, keep = recipe.plan(inp, {"sheet": {"asset_id": "a1"}}, reg)
    assert draw.input["prompt"].startswith(
        "Character reference sheet of @griffin: one plain light grey canvas"
    )
    assert {k: v for k, v in draw.input.items() if k not in ("params", "prompt")} == {
        "model_id": "nano-banana-pro",
        "mode": "edit",
        "media": [],
    }
    assert keep.input == {
        "op": "add_element_image",
        "element_id": str(eid),
        "asset_id": "a1",
        "view": "sheet",
        "first": True,
    }


@pytest.mark.parametrize("sheet", ["expressions", "poses"])
def test_a_grid_sheet_draws_eight_cells_without_text_and_is_never_attached(sheet):
    reg, eid = get_registry(), uuid.uuid4()
    eight = cells(sheet)
    if sheet == "expressions":  # one feeling of each family of the director's emotion wheel, its middle level
        assert eight == [e.label.lower() for e in get_library().emotions if e.level == 2]
        assert eight == ["joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation"]
    else:
        assert eight == list(POSES)
    assert len(set(eight)) == 8
    prompt = sheet_prompt(sheet, "griffin")
    assert all(c in prompt for c in eight) and "four columns and two rows" in prompt and "no text" in prompt
    draw, keep = get_recipe("character_sheet").plan(
        CharacterSheetInput(element_id=eid, slug="griffin", sheet=sheet), {"sheet": {"asset_id": "a1"}}, reg
    )
    assert draw.input["prompt"] == prompt
    assert keep.input == {"op": "add_element_image", "element_id": str(eid), "asset_id": "a1", "view": sheet}
    assert set(GRIDS) == set(UNATTACHED_VIEWS)


def test_a_look_is_drawn_from_the_character_and_kept_as_the_look():
    reg, eid = get_registry(), uuid.uuid4()
    recipe = get_recipe("character_look")
    inp = CharacterLookInput(
        character="maya",
        element_id=eid,
        look="winter",
        name="Winter",
        outfit="a red wool coat and  boots",
        count=2,
    )
    assert recipe.validate(inp, reg) == []
    assert worded(inp) == "wearing a red wool coat and boots"  # the outfit, when no description is given
    first, second = recipe.plan(inp, {}, reg)
    assert (first.key, second.key) == ("look[00]", "look[01]") and first.optional and second.optional
    prompt = first.input["prompt"]
    assert prompt.startswith("Full-body picture of @maya, wearing a red wool coat and boots, standing, ")
    assert "same face" in prompt and "a photograph stays a photograph" in prompt
    assert "direction" not in first.input and first.input["prompt"] != second.input["prompt"]  # another angle

    settled = {"look[00]": {"asset_id": "a1"}, "look[01]": {"failed": "refused"}}
    planned = {s.key: s for s in recipe.plan(inp, settled, reg)}
    assert "keep[01]" not in planned
    assert planned["keep[00]"].input == {
        "op": "add_element_image",
        "element_id": str(eid),
        "look": "winter",
        "look_name": "Winter",
        "look_description": "wearing a red wool coat and boots",
        "asset_id": "a1",
        "view": ANGLES[0][1],
    }
    assert planned["keep[00]"].depends_on == ("look[00]",)
    assert recipe.result(inp, settled) is None  # not kept yet
    assert recipe.result(inp, {**settled, "keep[00]": {"asset_id": "a1"}}) == {
        "look": "winter",
        "asset_ids": ["a1"],
    }
    assert recipe.failure(inp, settled) is None
    assert recipe.failure(inp, {"look[00]": {"failed": "x"}, "look[01]": {"failed": "x"}})


def test_another_style_names_the_style_and_lets_the_medium_go():
    reg = get_registry()
    recipe = get_recipe("character_look")
    style = get_library().styles[0].id
    inp = CharacterLookInput(
        character="maya", look="styled", name="Styled", description="drawn in its style", style=style
    )
    assert recipe.validate(inp, reg) == []
    (draw,) = recipe.plan(inp, {}, reg)
    assert draw.input["direction"] == {"style": style}
    assert "photograph" not in draw.input["prompt"]  # the style says what it's drawn in
    assert "face, hair, build, skin and clothing" in draw.input["prompt"]  # no outfit: it keeps its clothes
    mine = inp.model_copy(update={"style": "@my-ink"})  # a saved style (My styles) goes the same way
    assert recipe.validate(mine, reg) == [] and recipe.plan(mine, {}, reg)[0].input["direction"] == {
        "style": "@my-ink"
    }

    assert recipe.validate(inp.model_copy(update={"style": "no-such-style"}), reg) == [
        "unknown style 'no-such-style'"
    ]
    assert recipe.validate(inp.model_copy(update={"style": ""}), reg) == [
        "say what the look wears, or pick a style to draw it in"
    ]
    assert (
        "can't draw from reference pictures"
        in recipe.validate(inp.model_copy(update={"model_id": "veo-3.1"}), reg)[0]
    )
    with pytest.raises(ValidationError):
        CharacterLookInput(character="maya", look="Winter Coat", name="Winter", outfit="a coat")
    with pytest.raises(ValidationError):
        CharacterLookInput(character="maya", look="winter", name="Winter", outfit="a coat", count=5)


def test_a_take_is_priced_with_the_pictures_its_mentions_attach():
    """A character's two main pictures and a saved style's two, within the mode's reference slot: a model that
    charges per input picture (GPT Image 2.5) isn't quoted for one."""
    from studio.pipelines.recipes.common import mention_refs
    from studio.pipelines.recipes.compare import CompareInput

    reg = get_registry()
    gpt, ref = reg.models["gpt-image-2.5"], "image_references"
    look = CharacterLookInput(
        character="maya",
        element_id=uuid.uuid4(),
        look="winter",
        name="Winter",
        outfit="a coat",
        model_id="gpt-image-2.5",
        count=2,
    )
    recipe = get_recipe("character_look")
    assert [j.roles for j in recipe.jobs(look, reg)] == [(ref,) * 2] * 2
    assert recipe.jobs(look.model_copy(update={"style": "@inky"}), reg)[0].roles == (ref,) * 4
    catalog = get_library().styles[0].id  # a catalog style is words only
    assert recipe.jobs(look.model_copy(update={"style": catalog}), reg)[0].roles == (ref,) * 2

    sheet = CharacterSheetInput(element_id=uuid.uuid4(), slug="maya", image_model_id="gpt-image-2.5")
    assert get_recipe("character_sheet").jobs(sheet, reg)[0].roles == (ref,) * 2
    photos = get_recipe("character_photos")
    assert (
        photos.jobs(photos.Input(element_id=uuid.uuid4(), slug="maya", count=1), reg)[0].roles == (ref,) * 2
    )

    # never past the slot, and nothing on a mode without references
    assert mention_refs(gpt, "edit", characters=1, style="@inky", have=[ref] * 15) == (ref,)
    assert mention_refs(gpt, "t2i", characters=1, style="@inky") == ()
    # compare adds a saved style's pictures to the ones given
    both = CompareInput(
        prompt="a lighthouse",
        media=[{"role": ref, "asset_id": str(uuid.uuid4())}],
        entries=[
            {"model_id": "gpt-image-2.5", "mode": "edit", "direction": {"style": "@inky"}},
            {"model_id": "gpt-image-2.5", "mode": "edit"},
        ],
    )
    assert [j.roles for j in get_recipe("compare").jobs(both, reg)] == [(ref,) * 3, (ref,)]
