"""The character designer's vocabulary and starters (registry/characters/), their pictures, Claude's draft from a
photo or a sentence (its answer model, its mocks and how an answer becomes designer picks), and how a designed
character reaches prompts (its description in a mention, its expression in its portrait)."""

from __future__ import annotations

import logging
import re
import shutil
import typing
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_style_fanout import Answers

from studio.characters import traits as vocab
from studio.characters.traits import CREATURE, get_characters, load_characters, slug
from studio.characters.writer import _draft, _mock_photo, _mock_text, answer_fields, answer_model
from studio.config import get_settings
from studio.db.models import ElementKind
from studio.director.elements import DESCRIPTION_LIMIT, resolve_mentions
from studio.director.library import get_library
from studio.llm import pricing
from studio.pipelines.recipes.cinema_cast import CinemaCastInput, CinemaCastRecipe
from studio.registry.loader import get_registry

REPO = Path(__file__).resolve().parents[4]
PUBLIC = REPO / "apps" / "web" / "public"
ROOT = get_settings().resolve_registry_dir()
CAT = get_characters()
STYLES = get_library().styles
FIELDS = answer_fields(CAT)
ANSWER = answer_model(tuple(CAT.presentations), FIELDS, tuple(s.id for s in STYLES))

# What saved characters hold in `attributes.traits` from before the vocabulary moved here: never reworded or dropped
SAVED = {
    "age": ["young", "teenage", "twenty-something", "thirty-something", "middle-aged", "elderly"],
    "build": ["slight", "athletic", "average", "broad", "heavyset", "very tall"],
    "hair": [
        "short curly",
        "long straight",
        "shaved",
        "braided",
        "messy shoulder-length",
        "slicked back",
        "bald",
    ],
    "hair_color": ["black", "dark brown", "auburn", "blonde", "silver", "dyed blue", "bright red"],
    "eyes": ["brown", "hazel", "green", "blue", "grey", "amber"],
    "outfit": [
        "worn leather jacket",
        "tailored suit",
        "streetwear hoodie",
        "flowing robe",
        "spacesuit",
        "work overalls",
        "plate armour",
        "cowboy duster",
        "naval uniform",
        "evening dress",
        "pilot's flight suit",
        "fencing whites",
        "long trench coat",
        "lab coat",
        "desert nomad wraps",
    ],
    "detail": [
        "scar across one eyebrow",
        "freckles",
        "round glasses",
        "facial tattoo",
        "eye patch",
        "silver earrings",
        "missing tooth",
    ],
    "archetype": ["hero", "rebel", "mentor", "trickster", "villain", "everyday person"],
}
SAVED_CREATURE = {
    "body": [
        "two-legged",
        "four-legged",
        "six-legged",
        "winged",
        "snake-like",
        "finned",
        "tentacled",
        "blob-like",
        "plant-like",
        "golem-like",
    ],
    "likeness": [
        "wolf",
        "big cat",
        "bear",
        "owl",
        "lizard",
        "frog",
        "octopus",
        "moth",
        "beetle",
        "deer",
        "shark",
    ],
    "size": ["pocket-sized", "cat-sized", "human-sized", "horse-sized", "towering"],
    "stage": ["baby", "young", "adult", "ancient"],
    "covering": [
        "fur",
        "scales",
        "feathers",
        "chitin",
        "smooth skin",
        "bark and moss",
        "crystal",
        "armour plates",
        "translucent jelly",
    ],
    "coloring": [
        "camouflage",
        "striped",
        "spotted",
        "iridescent",
        "bioluminescent",
        "pale",
        "two-tone",
        "vivid",
    ],
    "features": [
        "horns",
        "antlers",
        "neck frill",
        "tusks",
        "beak",
        "mandibles",
        "wings",
        "clubbed tail",
        "extra arms",
    ],
    "eyes": ["two eyes", "single eye", "many eyes", "eyestalks", "no visible eyes", "glowing eyes"],
    "temperament": ["adorable", "majestic", "eerie", "fearsome", "comical", "wise"],
    "habitat": [
        "forests",
        "deserts",
        "the deep sea",
        "arctic ice",
        "volcanic lands",
        "caves",
        "high skies",
        "swamps",
    ],
    "gear": ["pack harness", "rider's saddle", "battle armour", "jewelled collar", "robes", "tech implants"],
}


# ---------------------------------------------------------------- the vocabulary and its pictures


def test_saved_characters_keep_their_options():
    assert CAT.presentations == ["woman", "man", "androgynous person", CREATURE]
    for traits, saved in ((CAT.person, SAVED), (CAT.creature, SAVED_CREATURE)):
        offered = {t.key: t.every_option for t in traits}
        for key, options in saved.items():
            assert [o for o in options if o not in offered.get(key, [])] == [], key
    outfit = next(t for t in CAT.person if t.key == "outfit")
    assert outfit.options_for(None) == outfit.options_for("man")  # with none picked, the man's list
    assert "evening dress" in outfit.options_for("woman") and "evening dress" not in outfit.options_for("man")
    assert [t.key for t in CAT.creature if t.multi] == ["features"]


def test_the_new_person_traits_and_their_wording():
    by_key = {t.key: t for t in CAT.person}
    assert by_key["expression"].options == [
        "smiling",
        "serious",
        "smirking",
        "surprised",
        "tired",
        "determined",
    ]
    assert by_key["expression"].phrase == "a {o} expression"
    assert by_key["accessory"].phrase == "with {a}"
    assert by_key["accessory"].overrides == {"headphones": "with headphones around the neck"}
    assert (by_key["palette"].label, by_key["palette"].phrase) == ("Colors", "dressed in {o}")


def test_every_option_has_its_picture_in_both_sizes():
    """designer/<set>/<key>-<slug>.webp (256 px) and full/ (768 px) for each set an option shows in: the Presentation
    row in man/, a person's options in the three person sets (each set's own outfits), a creature's in its own."""
    wanted = {("man", "presentation", p) for p in CAT.presentations}
    for presentation in CAT.presentations:
        for t in CAT.traits_for(presentation):
            wanted |= {(slug(presentation), t.key, o) for o in t.options_for(presentation)}
    designer = PUBLIC / "designer"
    missing = sorted(
        f"{s}/{full}{key}-{slug(o)}.webp"
        for s, key, o in wanted
        for full in ("", "full/")
        if not (designer / s / f"{full}{key}-{slug(o)}.webp").is_file()
    )
    assert not missing, f"draw pictures for {missing}"
    assert {s for s, _, _ in wanted} == {"man", "woman", "androgynous-person", "non-human-creature"}


def test_every_starter_has_its_portrait_and_fits_the_vocabulary():
    assert len(CAT.starters) == 8
    missing = [
        s.id for s in CAT.starters if not (PUBLIC / "starters" / "characters" / f"{s.id}.webp").is_file()
    ]
    assert not missing, f"draw portraits for {missing}"
    styles = {s.id for s in STYLES}
    for s in CAT.starters:
        assert s.style in styles and s.traits["presentation"] in CAT.presentations
        keys = {t.key for t in CAT.traits_for(s.traits["presentation"])}
        assert set(s.traits) - {"presentation"} <= keys


# ---------------------------------------------------------------- loading


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A copy of registry/characters that get_characters() reads instead, with nothing loaded from it yet."""
    root = tmp_path / "registry"
    shutil.copytree(ROOT / "characters", root / "characters")
    monkeypatch.setattr(vocab, "get_settings", lambda: SimpleNamespace(resolve_registry_dir=lambda: root))
    monkeypatch.setattr(vocab, "_good", {})
    monkeypatch.setattr(vocab, "_tried", {})
    return root


def edit(root: Path, name: str, change) -> None:
    path = root / "characters" / name
    path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        (
            "starters.yaml",
            lambda s: s.replace("hair_color: dyed blue", "hair_color: teal"),
            "starter kai-mendes: hair_color 'teal' isn't an option for a man",
        ),
        (
            "starters.yaml",
            lambda s: s.replace("outfit: fencing whites", "outfit: naval uniform"),
            "starter rosa-delgado: outfit 'naval uniform' isn't an option for a woman",
        ),
        (
            "starters.yaml",
            lambda s: s.replace("features: antlers", "features: antlers, fins"),
            "starter bramble: features 'fins' isn't an option",
        ),
        (
            "starters.yaml",
            lambda s: s.replace("      body: golem-like", "      build: broad"),
            "starter bramble: a non-human creature has no trait 'build'",
        ),
        (
            "starters.yaml",
            lambda s: s.replace("style: anime", "style: anime-2"),
            "starter kai-mendes: unknown style 'anime-2'",
        ),
        (
            "starters.yaml",
            lambda s: s.replace("voice_id: IKne3meq5aSn9XLyUdCD", "voice_id: nobody"),
            "starter kai-mendes: 'nobody' isn't one of eleven-v4's voices",
        ),
        ("starters.yaml", lambda s: s.replace("id: pip", "id: bramble"), "duplicate ids ['bramble']"),
        (
            "traits.yaml",
            lambda s: s.replace("[slight, athletic,", "[slight, athletic, slight,"),
            "build: options ['slight'] repeated",
        ),
        (
            "traits.yaml",
            lambda s: s.replace('phrase: "{o} build"', 'phrase: "sturdy build"'),
            "build: phrase 'sturdy build' needs {o} or {a}",
        ),
        (
            "traits.yaml",
            lambda s: s.replace('phrase: "{o} build"', 'phrase: "{x} build"'),
            "build: ['{x} build'] take no placeholders but {o} and {a}",
        ),
        (
            "traits.yaml",
            lambda s: s.replace("overrides: {headphones:", "overrides: {earmuffs:"),
            "accessory: overrides for options it doesn't have: ['earmuffs']",
        ),
        ("traits.yaml", lambda s: s.replace("key: hair_color", "key: style"), "['style'] can't be keys"),
        (
            "traits.yaml",
            lambda s: s.replace("      woman: [evening dress", "      lady: [evening dress"),
            "person trait outfit: options for ['lady'], which take no person traits",
        ),
        (
            "traits.yaml",
            lambda s: s.replace(", non-human creature]", "]"),
            "presentations must be different and include 'non-human creature'",
        ),
        ("traits.yaml", lambda s: "presentations: [broken", "characters/traits.yaml: not valid YAML"),
    ],
)
def test_a_broken_file_says_which_and_why(registry, name, change, message):
    edit(registry, name, change)
    with pytest.raises(ValueError) as exc:
        load_characters(registry)
    assert message in str(exc.value)


def test_a_broken_change_keeps_the_last_good_catalog(registry, caplog):
    good = get_characters()
    edit(registry, "starters.yaml", lambda s: s.replace("hair_color: dyed blue", "hair_color: teal"))
    with caplog.at_level(logging.ERROR, logger=vocab.__name__):
        assert get_characters() is good and get_characters() is good  # the routes keep working
    logged = [r.getMessage() for r in caplog.records if "keeping the last good one" in r.getMessage()]
    assert len(logged) == 1 and "kai-mendes" in logged[0]  # once per change, naming the starter

    edit(registry, "starters.yaml", lambda s: s.replace("hair_color: teal", "hair_color: bright red"))
    assert (
        next(s for s in get_characters().starters if s.id == "kai-mendes").traits["hair_color"]
        == "bright red"
    )


def test_with_nothing_loaded_yet_a_broken_catalog_raises(registry):
    edit(registry, "traits.yaml", lambda s: "presentations: [broken")
    for _ in range(2):  # each time: a failure is never kept as if it had loaded
        with pytest.raises(ValueError, match=r"characters/traits\.yaml: not valid YAML"):
            get_characters()


def test_a_style_that_goes_or_comes_checks_the_starters_again(registry, monkeypatch, caplog):
    """The starters are checked against the styles (and the registry's voices) too, not only their own files."""
    lib, good = get_library(), get_characters()
    without = lib.model_copy(update={"styles": [s for s in lib.styles if s.id != "anime"]})
    monkeypatch.setattr(vocab, "get_library", lambda: without)
    with caplog.at_level(logging.ERROR, logger=vocab.__name__):
        assert get_characters() is good and get_characters() is good  # the last good one serves
    logged = [r.getMessage() for r in caplog.records if "keeping the last good one" in r.getMessage()]
    assert len(logged) == 1 and "starter kai-mendes: unknown style 'anime'" in logged[0]

    # a starter saved before its style's file: the style arriving loads it, with its own files unchanged since
    edit(registry, "starters.yaml", lambda s: s.replace("style: anime", "style: anime-2"))
    assert get_characters() is good
    renamed = [s.model_copy(update={"id": "anime-2"}) if s.id == "anime" else s for s in lib.styles]
    monkeypatch.setattr(vocab, "get_library", lambda: lib.model_copy(update={"styles": renamed}))
    assert next(s for s in get_characters().starters if s.id == "kai-mendes").style == "anime-2"


# ---------------------------------------------------------------- in prompts


async def test_a_long_description_reaches_a_mention_in_whole_phrases():
    """A first @mention carries at most DESCRIPTION_LIMIT characters of the description, ending after a whole phrase
    (a designer description is a list of them), never mid-word."""
    words = ", ".join(f"trait number {i}" for i in range(40))
    nova = SimpleNamespace(
        id=uuid.uuid4(),
        slug="nova",
        name="Nova",
        description=words,
        kind=ElementKind.character,
        voice={},
        lora={},
    )
    spec = get_registry().model("nano-banana-2")
    prompt = (
        await resolve_mentions(Answers([nova], []), uuid.uuid4(), spec, "t2i", "@nova waves", [])
    ).prompt
    said = prompt.removeprefix("Nova, ").removesuffix(", waves")
    assert len(said) <= DESCRIPTION_LIMIT and re.fullmatch(r"(trait number \d+, )*trait number \d+", said)


def test_a_designed_characters_portrait_shows_its_expression():
    """The expression stays out of the saved description, which every mention carries, so the portrait gets it."""
    inp = CinemaCastInput(
        characters=[
            {"element_id": uuid.uuid4(), "slug": "iris", "expression": "smiling"},
            {"element_id": uuid.uuid4(), "slug": "pip"},  # a film's new cast: the portrait as it always was
        ]
    )
    prompts = [s.input["prompt"] for s in CinemaCastRecipe().plan(inp, {}, get_registry())]
    assert "head and shoulders, smiling expression, looking at the camera" in prompts[0]
    assert prompts[1] == (
        "Character reference portrait of @pip: front view, head and shoulders, neutral expression, looking at the "
        "camera, plain light grey background, soft even light, sharp focus on the face."
    )


# ---------------------------------------------------------------- Claude's draft


def literal(field: str) -> tuple:
    annotation = ANSWER.model_fields[field].annotation
    if typing.get_origin(annotation) is list:
        annotation = typing.get_args(annotation)[0]
    return typing.get_args(annotation)


def test_claude_answers_from_the_vocabulary_in_one_flat_model():
    person = {t.key: t for t in CAT.person}
    assert literal("presentation") == tuple(
        CAT.presentations
    )  # always one: a person's or a creature's traits
    assert literal("age") == (*person["age"].options, "")
    assert set(literal("outfit")) == {*person["outfit"].every_option, ""}  # every presentation's outfits
    eyes = next(t for t in CAT.creature if t.key == "eyes")
    assert literal("eyes") == (*person["eyes"].options, *eyes.options, "")  # the key both kinds have
    assert typing.get_origin(ANSWER.model_fields["features"].annotation) is list and "" not in literal(
        "features"
    )
    assert literal("style") == (*(s.id for s in STYLES), "")
    assert all(f.is_required() for f in ANSWER.model_fields.values())  # flat, nothing optional
    # built once per vocabulary
    assert answer_model(tuple(CAT.presentations), answer_fields(CAT), tuple(s.id for s in STYLES)) is ANSWER


def test_the_text_mock_picks_the_options_a_sentence_names():
    def draft(text):
        return _draft(CAT, ANSWER(**_mock_text(CAT, FIELDS, STYLES, text)))

    d = draft("elderly woman with silver hair")
    assert {k: v for k, v in d.traits.items() if v} == {
        "presentation": "woman",
        "age": "elderly",
        "hair_color": "silver",
    }
    assert set(d.traits) == {"presentation", *(t.key for t in CAT.person)}  # every trait: "" is Any
    assert d.name and d.style == "" and draft("elderly woman with silver hair") == d  # the same each time

    d = draft("An anime man with dark brown hair and brown eyes")
    assert (d.traits["hair_color"], d.traits["eyes"], d.style) == (
        "dark brown",
        "brown",
        "anime",
    )  # each word once
    d = draft("a dragon covered in scales, with horns and wings")
    assert d.traits["presentation"] == CREATURE and d.traits["features"] == "horns, wings"
    assert "age" not in d.traits and d.traits["covering"] == "scales"


def test_the_photo_mock_is_a_starter_chosen_by_the_picture():
    a = _draft(CAT, ANSWER(**_mock_photo(CAT, FIELDS, "0123abcd")))
    assert a == _draft(CAT, ANSWER(**_mock_photo(CAT, FIELDS, "0123abcd")))
    starter = next(s for s in CAT.starters if s.name == a.name)
    assert {k: v for k, v in a.traits.items() if v} == starter.traits and a.style == starter.style
    others = {_draft(CAT, ANSWER(**_mock_photo(CAT, FIELDS, h))).name for h in ("a", "b", "c", "d")}
    assert len(others) > 1


def test_a_draft_keeps_only_what_its_presentation_offers():
    answer = ANSWER(
        **{
            **_mock_photo(CAT, FIELDS, None),
            "presentation": "man",
            "outfit": "evening dress",
            "age": "young",
            "covering": "scales",
            "features": ["wings", "wings"],
            "name": "  Ada   Lovelace-Smith  ",
        }
    )
    d = _draft(CAT, answer)
    assert d.traits["presentation"] == "man" and d.traits["outfit"] == "" and d.traits["age"] == "young"
    assert "covering" not in d.traits and "features" not in d.traits
    assert d.name == "Ada Lovelace-Smith"
    creature = _draft(
        CAT,
        ANSWER(
            **{
                **_mock_photo(CAT, FIELDS, None),
                "presentation": CREATURE,
                "features": ["wings", "horns", "wings"],
                "age": "young",
            }
        ),
    )
    assert creature.traits["features"] == "wings, horns" and "age" not in creature.traits


def test_the_two_drafts_are_priced_as_typical_calls():
    assert pricing.TYPICAL_CALLS["character.from_photo"] == ("fast", 4_200, 800)
    assert pricing.TYPICAL_CALLS["character.from_text"] == ("fast", 2_500, 800)
