"""The picture style catalog (registry/styles/<category>/<id>.yaml) that Create image, the character designer and
Faceless Studio share, and how a shot's `direction.style` is written into each model's prompt."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.config import get_settings
from studio.director import library
from studio.director.compiler import ShotDirection, compile_prompt, unknown_ids
from studio.director.library import STYLE_CATEGORIES, get_library, load_library
from studio.domain.generations import compile_shots
from studio.faceless.catalog import get_catalog
from studio.llm.enhance import SYSTEM, build_request
from studio.pipelines.recipes.cinema_cast import CinemaCastInput, CinemaCastRecipe
from studio.registry.lint import lint_dialects
from studio.registry.loader import load_registry
from studio.registry.schema import ShotsParam
from studio.registry.validate import RequestError

REPO = Path(__file__).resolve().parents[4]
ROOT = get_settings().resolve_registry_dir()
REG = load_registry(ROOT)
LIB = get_library()
FACELESS = {
    "wildlife-documentary",
    "editorial-motion-graphics",
    "fairy-tale-myth",
    "hand-drawn",
    "pastel-flat-2d",
    "stickman-cartoon",
    "colorful-3d",
    "mannequin",
    "paper-collage",
    "paper-diorama",
    "studio-3d",
    "watercolor-chronicle",
    "poster-vector",
}  # stored Faceless runs and channels name these
PROMPT = "a lighthouse keeper climbs the stairs"
WATERCOLOR = LIB.look("styles", "watercolor-chronicle").prompt  # type: ignore[union-attr]  # a lowercase fragment
ANIME = LIB.look("styles", "anime").prompt  # type: ignore[union-attr]  # a sentence with its full stop
WATERCOLOR_FILE = "styles/painting/watercolor-chronicle.yaml"


def compiled(model_id: str, direction: ShotDirection):
    return compile_prompt(REG.model(model_id), PROMPT, direction)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the catalog


def test_every_style_has_its_six_samples():
    """The gallery shows styles/<id>-<subject>.webp (256 px) and zooms to styles/full/<id>-<subject>.webp (768 px)."""
    samples = REPO / "apps" / "web" / "public" / "styles"
    missing = [
        f"{folder}{s.id}-{subject}.webp"
        for s in LIB.styles
        for folder in ("", "full/")
        for subject in ("person", "place", "object")
        if not (samples / f"{folder}{s.id}-{subject}.webp").exists()
    ]
    assert not missing, f"draw samples for {missing}"


def test_each_file_names_its_style_and_category():
    files = sorted((ROOT / "styles").rglob("*.yaml"))
    assert len(LIB.styles) == len(files) == len({s.id for s in LIB.styles})  # every file loaded, no id twice
    assert {(s.category, s.id) for s in LIB.styles} == {(p.parent.name, p.stem) for p in files}
    assert {s.category for s in LIB.styles} == set(STYLE_CATEGORIES)
    # galleries list them by category, then label
    assert LIB.styles == sorted(
        LIB.styles, key=lambda s: (STYLE_CATEGORIES.index(s.category), s.label.casefold())
    )


def test_faceless_offers_exactly_the_styles_with_a_caption():
    assert {s.id for s in LIB.styles if s.caption} == FACELESS
    assert [s.id for s in get_catalog().styles] == [s.id for s in LIB.styles if s.caption]


def test_caption_fonts_have_a_linux_stand_in():
    conf = (REPO / "infra" / "server" / "fonts.conf").read_text(encoding="utf-8")
    missing = sorted(
        {s.caption.font for s in LIB.styles if s.caption and f"<family>{s.caption.font}</family>" not in conf}
    )
    assert not missing, (
        f"alias {missing} in infra/server/fonts.conf (libass on the server finds no Windows fonts)"
    )


# ---------------------------------------------------------------- compiling


def test_the_style_comes_right_after_the_prompt():
    d = ShotDirection(style="watercolor-chronicle", shot_size="wide", lighting="golden_hour")
    sentence = f"{WATERCOLOR[0].upper()}{WATERCOLOR[1:]}."
    # sentences: veo, runway, the image models and the default
    for model_id in ("veo-3.1", "runway-gen-4.5", "nano-banana-2", "flux-2-klein-4b", "luma-ray-3.2"):
        c = compiled(model_id, d)
        assert c.prompt.startswith(f"A lighthouse keeper climbs the stairs. {sentence} Wide shot."), model_id
        assert not c.warnings
    assert compiled("seedance-2.5", d).prompt.splitlines()[:3] == [
        "A lighthouse keeper climbs the stairs.",
        f"Style: {WATERCOLOR}.",
        "Shot: wide shot.",
    ]
    for model_id in ("kling-3.0", "wan-2.2-5b"):  # comma cues: the last one
        assert compiled(model_id, d).prompt.endswith(f"golden-hour sunlight, {WATERCOLOR}"), model_id

    # a style written as a sentence keeps one full stop
    anime = ShotDirection(style="anime")
    assert compiled("veo-3.1", anime).prompt == f"A lighthouse keeper climbs the stairs. {ANIME}"
    assert compiled("seedance-2.5", anime).prompt.splitlines()[1] == f"Style: {ANIME}"
    assert compiled("kling-3.0", anime).prompt == f"{PROMPT}, {ANIME.rstrip('.')}"


def test_a_style_alone_is_direction():
    assert not ShotDirection(style="anime").is_empty()


def test_a_style_that_isnt_a_photograph_drops_the_lens_and_camera():
    d = ShotDirection(
        style="watercolor-chronicle",
        lens="anamorphic",
        focal_length="normal_35",
        aperture="f2",
        camera_body="film_35",
        shot_size="wide",
    )
    c = compiled("kling-3.0", d)
    assert c.prompt == f"{PROMPT}, wide shot, {WATERCOLOR}"
    assert c.warnings == [
        "lens and camera choices don't apply to the Watercolor chronicle style and were left out"
    ]
    c = compiled("kling-3.0", d.model_copy(update={"style": "cinematic-still"}))  # a photograph keeps them
    assert "35mm lens" in c.prompt and "f/2" in c.prompt and not c.warnings


def test_an_unknown_style_is_reported_and_left_out():
    d = ShotDirection(style="claymation-9000")
    assert unknown_ids(d) == ["unknown style 'claymation-9000'"]
    c = compiled("veo-3.1", d)
    assert (c.prompt, c.warnings) == (
        "A lighthouse keeper climbs the stairs.",
        ["unknown style 'claymation-9000' ignored"],
    )


def test_the_style_is_never_shed():
    spec = REG.model("kling-3.0").model_copy(deep=True)  # type: ignore[union-attr]
    spec.prompt.max_chars = len(f"{PROMPT}, {WATERCOLOR}") + 5
    d = ShotDirection(style="watercolor-chronicle", shot_size="wide", lighting="golden_hour", genre="drama")
    c = compile_prompt(spec, PROMPT, d)
    assert c.prompt == f"{PROMPT}, {WATERCOLOR}" and len(c.warnings) == 3  # the framing, light and mood went

    spec.prompt.max_chars = len(PROMPT) + 10  # too short even with only the style left: it stays all the same
    c = compile_prompt(spec, PROMPT, d)
    assert c.prompt == f"{PROMPT}, {WATERCOLOR}" and c.warnings[-1].startswith("prompt is still")


def test_a_style_that_takes_a_shot_past_its_limit_says_so():
    """Kling 4.0 takes 512 characters a shot: a shot that fits on its own is refused for its style, saying so."""
    spec = REG.model("kling-4.0")
    param = next(p for p in spec.params_for("multi_shot") if isinstance(p, ShotsParam))  # type: ignore[union-attr]
    text = "a lighthouse keeper climbs the stairs, " * 10  # 390 characters
    shots = [{"prompt": text, "duration": 5}]
    out, _ = compile_shots(spec, param, shots, text, None, {})  # type: ignore[arg-type]
    assert out[0]["prompt"] == text.strip()
    with pytest.raises(
        RequestError,
        match=r"shot 1: with its direction it comes to \d+ characters and Kling 4\.0 "
        r"takes 512 a shot; shorten it or drop some of its style",
    ):
        compile_shots(spec, param, shots, text, {"style": "wildlife-documentary"}, {})  # type: ignore[arg-type]


def test_dialect_wording_must_name_a_dialect_in_use():
    assert lint_dialects(REG, LIB) == []
    lib = LIB.model_copy(deep=True)
    lib.styles[0].dialects = {"klingg": "x", "kling": "y", "default": "z"}
    assert lint_dialects(REG, lib) == [
        f"director styles {lib.styles[0].id}: dialect 'klingg' isn't one any model's prompt uses"
    ]


def test_enhance_leaves_the_style_to_the_direction():
    text = build_request(
        REG.model("flux-2-klein-4b"),
        "t2i",
        "a lighthouse",  # type: ignore[arg-type]
        ShotDirection(style="watercolor-chronicle"),
    )
    assert "Already handled by shot direction: picture style (Watercolor chronicle)." in text
    assert "never name a medium or art style" in SYSTEM
    # an id the catalog doesn't have adds no style, so the user's own medium words must stay
    unknown = build_request(
        REG.model("flux-2-klein-4b"),
        "t2i",
        "an oil painting of a lighthouse",  # type: ignore[arg-type]
        ShotDirection(style="watercolour"),
    )
    assert "picture style" not in unknown


def test_a_designed_characters_portrait_is_drawn_in_its_style():
    """The designer keeps the style out of the description, so the portrait drawn on saving gets it as direction."""
    inp = CinemaCastInput(
        characters=[
            {"element_id": uuid.uuid4(), "slug": "nova", "style": "anime"},
            {"element_id": uuid.uuid4(), "slug": "pip"},  # a film's new cast: no style
        ]
    )
    steps = CinemaCastRecipe().plan(inp, {}, REG)
    assert [s.input["direction"] for s in steps] == [{"style": "anime"}, None]


# ---------------------------------------------------------------- loading


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A copy of the vocabulary and styles that get_library() reads instead, with nothing loaded from it yet."""
    root = tmp_path / "registry"
    for name in ("director", "styles"):
        shutil.copytree(ROOT / name, root / name)
    monkeypatch.setattr(library, "get_settings", lambda: SimpleNamespace(resolve_registry_dir=lambda: root))
    monkeypatch.setattr(library, "_good", {})
    monkeypatch.setattr(library, "_tried", {})
    return root


def edit(root: Path, rel: str, change) -> None:
    path = root / rel
    path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s.replace("id: watercolor-chronicle", "id: watercolour"),
            "id 'watercolour' must be the file's name (watercolor-chronicle)",
        ),
        (
            lambda s: s.replace("category: painting", "category: retro"),
            "category 'retro' must be its folder's name (painting)",
        ),
        (
            lambda s: s.replace("chronicle illustration", "chronicle illustration, a logo in the corner"),
            "keep words like 'text' out of the prompt",
        ),
        (
            lambda s: s + "dialects: {kling: a logo in the corner}\n",
            "keep words like 'text' out of the prompt",
        ),
        (
            lambda s: s.replace("id: watercolor-chronicle", "id: watercolor--chronicle"),
            "should match pattern",
        ),
        (lambda s: s + "mood: grim\n", "mood"),
        (lambda s: "id: [broken", "not valid YAML"),
    ],
)
def test_a_broken_style_file_says_which_and_why(registry, change, message):
    edit(registry, WATERCOLOR_FILE, change)
    with pytest.raises(ValueError) as exc:
        load_library(registry)
    assert str(exc.value).startswith(f"{WATERCOLOR_FILE}: ") and message in str(exc.value)


@pytest.mark.parametrize("rel", ["styles/noir.yaml", "styles/photo/film/noir.yaml"])
def test_a_style_outside_its_category_folder_is_refused_not_skipped(registry, rel):
    path = registry / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "id: noir\nlabel: Noir\ncategory: photo\nsummary: Hard shadows.\nphotographic: true\n"
        "prompt: black-and-white film noir photograph\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError, match=rf"{rel}: category 'photo' must be its folder's name \({path.parent.name}\)"
    ):
        load_library(registry)


def test_two_styles_cant_share_an_id(registry):
    src = (registry / WATERCOLOR_FILE).read_text(encoding="utf-8")
    twin = registry / "styles" / "retro" / "watercolor-chronicle.yaml"
    twin.write_text(src.replace("category: painting", "category: retro"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"director styles: duplicate ids \['watercolor-chronicle'\]"):
        load_library(registry)


def test_an_added_or_removed_style_shows_at_once(registry):
    assert load_library(registry).look("styles", "sepia") is None
    added = registry / "styles" / "photo" / "sepia.yaml"
    added.write_text(
        "id: sepia\nlabel: Sepia\ncategory: photo\nsummary: Old brown prints.\nphotographic: true\n"
        "prompt: sepia-toned antique photograph\n",
        encoding="utf-8",
    )
    assert load_library(registry).look("styles", "sepia") is not None
    added.unlink()
    assert load_library(registry).look("styles", "sepia") is None


def test_a_broken_change_keeps_the_last_good_library(registry, caplog):
    good = get_library()
    edit(registry, WATERCOLOR_FILE, lambda s: "id: [broken")
    with caplog.at_level(logging.ERROR, logger=library.__name__):
        assert get_library() is good and get_library() is good  # routes keep working
    logged = [r.getMessage() for r in caplog.records if "keeping the last good one" in r.getMessage()]
    assert len(logged) == 1 and WATERCOLOR_FILE in logged[0]  # once per change, naming the file

    fixed = (
        (ROOT / WATERCOLOR_FILE)
        .read_text(encoding="utf-8")
        .replace("label: Watercolor chronicle", "label: Diary")
    )
    edit(registry, WATERCOLOR_FILE, lambda s: fixed)
    assert get_library().look("styles", "watercolor-chronicle").label == "Diary"  # type: ignore[union-attr]


def test_with_nothing_loaded_yet_a_broken_library_raises(registry):
    edit(registry, "director/looks.yaml", lambda s: "palettes: [broken")
    for _ in range(2):  # each time: a failure is never kept as if it had loaded
        with pytest.raises(ValueError, match=r"director/looks\.yaml: not valid YAML"):
            get_library()


def test_faceless_sees_a_new_caption_at_once(registry):
    assert get_catalog().style("anime") is None
    edit(registry, "styles/illustration/anime.yaml", lambda s: s + "caption: {font: Verdana}\n")
    assert get_catalog().style("anime").caption.font == "Verdana"  # type: ignore[union-attr]
