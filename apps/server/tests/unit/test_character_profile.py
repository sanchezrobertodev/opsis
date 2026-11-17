"""A character's profile (its limits, the block the writers read, Claude's draft and its mock), the likeness check's
mock, and the quick actions' recipe parts that need no database: `character_figure`'s modes and plan, and
`talking_shot` with a saved character. The routes and runs are in tests/integration/test_character_actions.py."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from studio.characters.likeness import mock_likeness
from studio.characters.profile import (
    BACKSTORY,
    CATCHPHRASE,
    PERSONALITY,
    SPEECH,
    ElementProfile,
    ProfileDraft,
    _clip,
    about,
    fitted,
    mock_profile,
    profile_block,
)
from studio.cinema import planner
from studio.db.models import Element, ElementKind
from studio.faceless import writer as faceless_writer
from studio.faceless.catalog import get_catalog
from studio.llm.client import LLMResult
from studio.llm.pricing import TYPICAL_CALLS
from studio.pipelines.recipes import get_recipe
from studio.pipelines.recipes import vlog as vlog_recipe
from studio.pipelines.recipes.character_figure import CharacterFigureInput, figure_mode, rig_mode
from studio.registry.loader import get_registry
from studio.registry.presets import render_template

REG = get_registry()
FIGURE = get_recipe("character_figure")
TALK = get_recipe("talking_shot")
FULL = {
    "personality": "warm, nosy and quick to laugh",
    "backstory": "Grew up above her parents' bakery.",
    "catchphrases": ["Butter fixes everything.", "Who's hungry?"],
    "speech": "fast, with baking metaphors",
}


def test_a_profile_holds_four_fields_within_their_limits():
    p = ElementProfile.model_validate({**FULL, "catchphrases": ["  Butter fixes everything. ", "", "   "]})
    assert p.catchphrases == ["Butter fixes everything."]  # blanks dropped, words trimmed
    assert ElementProfile().model_dump() == {
        "personality": "",
        "backstory": "",
        "catchphrases": [],
        "speech": "",
    }
    for bad in (
        {"personality": "x" * (PERSONALITY + 1)},
        {"backstory": "x" * (BACKSTORY + 1)},
        {"speech": "x" * (SPEECH + 1)},
        {"catchphrases": ["x" * (CATCHPHRASE + 1)]},
        {"catchphrases": [f"line {i}" for i in range(6)]},
        {"age": 30},
        {"catchphrases": [3]},
    ):
        with pytest.raises(ValidationError):
            ElementProfile.model_validate(bad)


def test_the_writers_read_a_short_block_and_nothing_for_an_empty_profile():
    assert profile_block(None) == profile_block({}) == profile_block(ElementProfile().model_dump()) == ""
    assert profile_block(FULL) == (
        "Personality: warm, nosy and quick to laugh\n"
        "How they talk: fast, with baking metaphors\n"
        'Catchphrases (one now and then, where it fits): "Butter fixes everything."; "Who\'s hungry?"'
    )
    assert "bakery" not in profile_block(FULL)  # the backstory stays in the editor
    assert profile_block({"speech": "  slow   and dry "}) == "How they talk: slow and dry"


def test_claudes_draft_is_held_to_the_profiles_limits():
    long = ProfileDraft(
        personality="word " * 100,
        backstory="tale " * 200,
        speech="talk " * 60,
        catchphrases=["", *[f"line {i} " + "again " * 30 for i in range(7)]],
    )
    p = fitted(long)
    assert len(p.personality) <= PERSONALITY and len(p.backstory) <= BACKSTORY and len(p.speech) <= SPEECH
    assert len(p.catchphrases) == 5 and all(0 < len(c) <= CATCHPHRASE for c in p.catchphrases)
    assert p.personality.endswith("word")  # cut after a whole word
    assert _clip("one two three", 9) == "one two" and _clip("abcdefghijk", 5) == "abcde"


def test_the_mock_draft_comes_from_the_name_and_description_and_never_changes():
    one = fitted(mock_profile("Mara Quill", "A tall woman in a red coat."))
    assert one == fitted(mock_profile("Mara Quill", "A tall woman in a red coat."))
    assert "Mara" in one.personality and "a tall woman in a red coat" in one.backstory
    assert one.catchphrases and one.speech
    assert fitted(mock_profile("Mara Quill", "x" * 600)).backstory  # a long description still fits
    assert fitted(mock_profile("", "")).personality


def test_the_draft_is_told_the_name_description_traits_and_looks():
    maya = Element(
        name="Maya Lind",
        description="a dancer with short black hair",
        kind=ElementKind.character,
        attributes={
            "traits": {"presentation": "woman", "age": "thirty-something", "hair": "", "style": "film-photo"}
        },
        looks={"winter": {"name": "Winter", "description": "wearing a red wool coat"}},
    )
    told = about(maya)
    assert "Name: Maya Lind" in told and "Description: a dancer with short black hair" in told
    assert "presentation: woman" in told and "age: thirty-something" in told
    assert (
        "hair:" not in told and "film-photo" not in told
    )  # blanks and the picture style aren't who they are
    assert "Looks they wear: Winter: wearing a red wool coat" in told


def test_the_new_claude_calls_are_priced():
    assert TYPICAL_CALLS["character.profile"] == ("fast", 900, 1_200)
    assert TYPICAL_CALLS["character.check"] == ("fast", 9_000, 800)


def test_the_likeness_mock_matches_unless_the_take_asks_otherwise():
    assert mock_likeness("Maya", "@maya waves").verdict == "match"
    off = mock_likeness("Maya", "@maya waves [[mock:inconsistent]]")
    assert off.verdict == "different" and {d.aspect for d in off.differences} == {"face", "hair"}


# ---------------------------------------------------------------- the profile reaches the writers


def capture(monkeypatch, module) -> list[str]:
    """The prompts a module sends Claude, answered by each call's own mock."""
    prompts: list[str] = []

    async def structured(session, user_id, *, prompt, mock, **_):
        prompts.append(prompt)
        return LLMResult(output=mock(), model="claude-test (mock)", mock=True)

    async def model_for(session, user_id, which):
        return "claude-test"

    monkeypatch.setattr(module, "structured", structured)
    monkeypatch.setattr(module, "model_for", model_for)
    return prompts


async def test_the_vlog_writer_writes_the_stars_lines_in_character(monkeypatch):
    prompts = capture(monkeypatch, vlog_recipe)
    await vlog_recipe.write_vlog(
        None,
        uuid.uuid4(),
        idea="a bake sale",
        shots=3,
        name="Maya",  # type: ignore[arg-type]
        profile=profile_block(FULL),
    )
    await vlog_recipe.write_vlog(None, uuid.uuid4(), idea="a bake sale", shots=3, name="Maya")  # type: ignore[arg-type]
    assert "Write their lines in character:\nPersonality: warm, nosy" in prompts[0]
    assert '"Butter fixes everything."' in prompts[0]
    assert "Personality" not in prompts[1]


async def test_a_faceless_star_keeps_in_character(monkeypatch):
    prompts = capture(monkeypatch, faceless_writer)
    theme = get_catalog().theme("creature-documentary")
    assert theme is not None
    await faceless_writer.write_script(
        None,
        uuid.uuid4(),
        topic="griffins",
        theme=theme,  # type: ignore[arg-type]
        minutes=0.5,
        star="Griffin",
        star_profile=profile_block(FULL),
    )
    await faceless_writer.write_script(
        None,
        uuid.uuid4(),
        topic="griffins",
        theme=theme,  # type: ignore[arg-type]
        minutes=0.5,
        star_profile=profile_block(FULL),
    )
    assert "Keep Griffin in character:\nPersonality:" in prompts[0]
    assert all("Personality" not in p for p in prompts[1:])  # no star, no profile


async def test_the_film_cast_reads_a_member_to_a_line_with_their_profile(monkeypatch):
    prompts = capture(monkeypatch, planner)
    cast = [("maya", f"Maya Lind. a baker\n{profile_block(FULL)}"), ("kai", "Kai. a fisherman")]
    await planner.plan_shots(None, uuid.uuid4(), script="Maya opens the shop.", cast=cast)  # type: ignore[arg-type]
    assert (
        "Cast (use these handles):\n- @maya: Maya Lind. a baker\n  Personality: warm, nosy and quick to laugh\n"
        "  How they talk: fast, with baking metaphors\n"
    ) in prompts[0]
    assert "\n- @kai: Kai. a fisherman\n" in prompts[0]


# ---------------------------------------------------------------- a 3D figure


def test_a_figure_is_built_by_modes_found_by_what_they_do():
    built = {m.id: figure_mode(m)[0].id for m in REG.models.values() if figure_mode(m)}  # type: ignore[index]
    assert built["tripo-h3.1"] == "image_to_3d"  # never the several-views or text modes
    assert set(built) >= {"tripo-h3.1", "trellis-2", "meshy-7.1", "hunyuan-3d-3.1", "tripo-p2"}
    assert all(REG.models[m].output_type == "3d" for m in built)
    assert (
        rig_mode(REG.models["meshy-rigging"]) is not None and figure_mode(REG.models["meshy-rigging"]) is None
    )
    assert figure_mode(REG.models["veo-3.1"]) is None and rig_mode(REG.models["tripo-h3.1"]) is None


def figure(**over) -> CharacterFigureInput:
    return CharacterFigureInput.model_validate(
        {"character": "maya", "element_id": str(uuid.uuid4()), "rig": True, **over}
    )


def made(*ids: str) -> dict:
    return {
        "generation_id": str(uuid.uuid4()),
        "asset_id": ids[0],
        "asset_ids": list(ids),
        "duration_ms": None,
    }


def test_a_figure_is_drawn_in_an_a_pose_built_rigged_and_kept_with_the_character():
    inp = figure()
    assert FIGURE.validate(inp, REG) == []
    jobs = FIGURE.jobs(inp, REG)
    assert [(j.model_id, j.mode) for j in jobs] == [
        ("nano-banana-pro", "edit"),
        ("tripo-h3.1", "image_to_3d"),
        ("meshy-rigging", "rig"),
    ]
    assert jobs[0].roles == ("image_references",) * 2  # priced with the two pictures the mention attaches

    first = FIGURE.plan(inp, {}, REG)
    assert [s.key for s in first] == ["pose"]
    pose = first[0].input
    assert (
        pose["media"] == [] and "@maya" in pose["prompt"] and "A-pose" in pose["prompt"]
    )  # the mention attaches

    settled = {"pose": made("p1")}
    steps = {s.key: s for s in FIGURE.plan(inp, settled, REG)}
    assert steps["model"].input["media"] == [{"role": "input_image", "asset_id": "p1"}]
    assert steps["model"].depends_on == ("pose",) and "rig" not in steps

    settled["model"] = made("glb")
    steps = {s.key: s for s in FIGURE.plan(inp, settled, REG)}
    assert steps["rig"].optional and steps["rig"].input["media"] == [
        {"role": "input_model", "asset_id": "glb"}
    ]
    assert "keep" not in steps and FIGURE.result(inp, settled) is None

    settled["rig"] = made("rigged", "walk", "run")
    keep = {s.key: s for s in FIGURE.plan(inp, settled, REG)}["keep"]
    assert keep.depends_on == ("model", "rig") and keep.input == {
        "op": "tag_assets",
        "asset_ids": ["glb", "rigged", "walk", "run"],
        "meta": {"element_id": str(inp.element_id), "character": "maya"},
    }
    settled["keep"] = {"asset_id": "glb", "asset_ids": ["glb", "rigged", "walk", "run"]}
    assert FIGURE.result(inp, settled) == {
        "asset_id": "rigged",
        "model_asset_id": "glb",
        "rigged": True,
        "element_id": str(inp.element_id),
    }


def test_a_figure_from_their_own_picture_or_left_unrigged():
    picture = str(uuid.uuid4())
    inp = figure(picture_asset_id=picture, rig=False)
    assert [j.model_id for j in FIGURE.jobs(inp, REG)] == ["tripo-h3.1"]
    (model,) = FIGURE.plan(inp, {}, REG)
    assert model.key == "model" and model.depends_on == () and model.input["media"][0]["asset_id"] == picture
    keep = FIGURE.plan(inp, {"model": made("glb")}, REG)[-1]
    assert keep.key == "keep" and keep.depends_on == ("model",) and keep.input["asset_ids"] == ["glb"]

    # a rig that fails (it's optional) still keeps the unrigged figure
    rigged = figure(picture_asset_id=picture)
    settled = {"model": made("glb"), "rig": {"failed": "not a body"}}
    keep = FIGURE.plan(rigged, settled, REG)[-1]
    assert keep.key == "keep" and keep.input["asset_ids"] == ["glb"]
    settled["keep"] = {"asset_id": "glb", "asset_ids": ["glb"]}
    assert FIGURE.result(rigged, settled)["rigged"] is False  # type: ignore[index]


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"model_id": "veo-3.1"}, "can't build a 3D model from a picture"),
        ({"model_id": "nope"}, "'nope' can't build a 3D model"),
        ({"rig_model_id": "tripo-h3.1"}, "can't rig a 3D character"),
        ({"image_model_id": "tripo-h3.1"}, "can't draw from reference pictures"),
    ],
)
def test_a_figure_needs_models_that_can_do_each_step(over, message):
    assert any(message in e for e in FIGURE.validate(figure(**over), REG)), FIGURE.validate(
        figure(**over), REG
    )


def test_a_figure_from_a_given_picture_needs_no_image_model_and_an_unrigged_one_no_rigger():
    assert (
        FIGURE.validate(
            figure(picture_asset_id=str(uuid.uuid4()), image_model_id="nope", rig=False, rig_model_id="nope"),
            REG,
        )
        == []
    )


# ---------------------------------------------------------------- a saved character says hello


def test_a_talking_shot_takes_a_saved_character_instead_of_a_picture():
    Input = TALK.Input
    models = [{"model_id": "kling-avatar-2"}]
    alone = Input.model_validate({"character": "maya", "script": "Hi, I'm Maya.", "models": models})
    assert alone.source == "image"
    # before resolve() fills in the picture, validation says what's missing
    assert any("@maya has no picture" in e for e in TALK.validate(alone, REG))
    # a stored run holds both (resolve wrote the picture in), and revise validates it again
    stored = Input.model_validate({**alone.model_dump(mode="json"), "image_asset_id": str(uuid.uuid4())})
    assert TALK.validate(stored, REG) == [] and stored.source == "image"
    assert TALK.title(stored).startswith("Talking shot: @maya: Hi")
    for bad in ({"character": "maya", "video_asset_id": str(uuid.uuid4())}, {}, {"character": "Maya Lind"}):
        with pytest.raises(ValidationError):
            Input.model_validate({"script": "Hi", "models": models, **bad})


def test_the_talking_photo_template_offers_a_saved_character():
    t = REG.templates["talking-photo"]
    assert [(i.key, i.alternative) for i in t.inputs] == [("image_asset_id", True), ("character", True)]
    out = render_template(t, {"script": "Hi, I'm Maya."}, {"character": "maya"})
    inp = TALK.Input.model_validate(out["input"])
    assert inp.character == "maya" and inp.image_asset_id is None and inp.script == "Hi, I'm Maya."
