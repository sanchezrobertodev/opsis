"""One prompt in several styles (compare entries with a direction and a label of their own), saved styles named as
`direction.style: "@slug"`, and the pictures Claude looks at to describe a style."""

from __future__ import annotations

import base64
import io
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from studio.db.models import ElementKind, StepKind
from studio.director.compiler import ShotDirection
from studio.director.elements import resolve_mentions, style_slug, with_style
from studio.domain import generations
from studio.domain.enums import AssetKind
from studio.domain.generations import CreateRequest
from studio.llm import images
from studio.llm.enhance import build_request
from studio.llm.styles import LOOK_LIMIT, MOCK_LOOKS, _mock, clip
from studio.pipelines.recipe import StepSpec
from studio.pipelines.recipes import cinema_cast
from studio.pipelines.recipes.cinema_cast import CinemaCastInput, CinemaCastRecipe
from studio.pipelines.recipes.compare import CompareInput, CompareRecipe, take_key
from studio.registry.loader import get_registry

REPO = Path(__file__).resolve().parents[4]
REG = get_registry()
RECIPE = CompareRecipe()
IMAGE = {"model_id": "nano-banana-2", "mode": "t2i"}


def plan_before_styles(inp: CompareInput) -> list[StepSpec]:
    """compare's take steps as they were planned before entries had a direction and a label."""
    out = []
    for i, e in enumerate(inp.entries, 1):
        mode = RECIPE._mode(inp, REG, e)
        assert mode is not None
        out.append(
            StepSpec(
                take_key(i, e.model_id),
                StepKind.generate,
                REG.models[e.model_id].name,
                {
                    "model_id": e.model_id,
                    "mode": mode,
                    "prompt": inp.prompt,
                    "params": e.params,
                    "media": RECIPE._media(inp, REG, e, mode),
                    "direction": inp.direction,
                },
                optional=True,
            )
        )
    return out


@pytest.mark.parametrize("direction", [None, {"style": "watercolor", "lighting": "golden_hour"}])
def test_a_compare_without_entry_directions_plans_exactly_as_before(direction):
    inp = CompareInput(
        prompt="a paper boat in the rain",
        direction=direction,
        entries=[
            {"model_id": "kling-3.0"},
            {"model_id": "seedance-2.5"},
            {"model_id": "veo-3.1", "params": {"duration": 4}},
        ],
    )
    steps = RECIPE.plan(inp, {}, REG)
    assert steps == plan_before_styles(inp)
    assert [s.digest() for s in steps] == [
        s.digest() for s in plan_before_styles(inp)
    ]  # old runs keep their takes
    settled = {s.key: {"asset_id": f"a{i}"} for i, s in enumerate(steps)}
    pick = RECIPE.plan(inp, settled, REG)[-1]
    assert [o["label"] for o in pick.input["options"]] == ["Kling 3.0", "Seedance 2.5", "Veo 3.1"]


def test_each_style_is_a_take_with_its_own_direction_and_name():
    inp = CompareInput(
        prompt="a lighthouse on a cliff",
        direction={"lighting": "golden_hour", "style": "watercolor"},
        entries=[
            {**IMAGE, "direction": {"style": "oil-painting"}, "label": "Oil painting"},
            {**IMAGE, "direction": {"style": "@night-ink"}, "label": "Night ink"},
            IMAGE,
        ],
    )
    steps = RECIPE.plan(inp, {}, REG)
    assert [s.key for s in steps] == [take_key(i, "nano-banana-2") for i in (1, 2, 3)]
    assert [s.label for s in steps] == ["Oil painting", "Night ink", "Nano Banana 2"]
    assert [s.input["direction"] for s in steps] == [
        {"lighting": "golden_hour", "style": "oil-painting"},
        {"lighting": "golden_hour", "style": "@night-ink"},
        {"lighting": "golden_hour", "style": "watercolor"},
    ]
    assert RECIPE.validate(inp, REG) == []
    settled = {s.key: {"asset_id": f"a{i}"} for i, s in enumerate(steps)}
    pick = RECIPE.plan(inp, settled, REG)[-1]
    assert [o["label"] for o in pick.input["options"]] == ["Oil painting", "Night ink", "Nano Banana 2"]


def test_a_style_typo_or_a_broken_direction_is_refused_before_anything_runs():
    inp = CompareInput(
        prompt="x",
        entries=[
            {**IMAGE, "direction": {"style": "oil-paintng"}},
            {**IMAGE, "direction": {"camera_moves": "pan"}},
            {**IMAGE, "direction": {"style": "@anyone"}},
        ],
    )  # saved: checked in resolve()
    errors = RECIPE.validate(inp, REG)
    assert errors[0] == "unknown style 'oil-paintng'"
    assert len(errors) == 2 and errors[1].startswith("direction:") and "camera_moves" in errors[1]


def test_up_to_six_takes_and_a_style_take_is_priced_like_a_plain_one_in_a_text_mode():
    CompareInput(prompt="x", entries=[IMAGE] * 6)
    with pytest.raises(ValidationError):
        CompareInput(prompt="x", entries=[IMAGE] * 7)
    plain = CompareInput(prompt="x", entries=[IMAGE] * 2)
    styled = CompareInput(
        prompt="x",
        entries=[{**IMAGE, "direction": {"style": s}, "label": s} for s in ("watercolor", "@night-ink")],
    )
    assert RECIPE.jobs(styled, REG) == RECIPE.jobs(plain, REG)


def test_a_saved_styles_pictures_are_priced_where_the_mode_takes_references():
    edit = {"model_id": "nano-banana-2", "mode": "edit"}

    def roles(style: str, pictures: int = 1) -> tuple[str, ...]:
        media = [{"role": "image_references", "asset_id": str(uuid.uuid4())} for _ in range(pictures)]
        inp = CompareInput(prompt="x", media=media, entries=[{**edit, "direction": {"style": style}}, edit])
        return RECIPE.jobs(inp, REG)[0].roles

    assert roles("@night-ink") == ("image_references",) * 3  # yours, then two of the style's
    assert roles("watercolor") == ("image_references",)  # a catalog style is words only
    assert len(roles("@night-ink", pictures=9)) == 10  # as many as the slot has room for


async def test_a_portrait_in_a_saved_style_thats_gone_is_drawn_without_it(monkeypatch):
    async def unknown(session, user_id, slugs):
        return [s for s in slugs if s == "gone"]

    monkeypatch.setattr(cinema_cast, "unknown_styles", unknown)
    inp = CinemaCastInput(
        characters=[
            {"element_id": uuid.uuid4(), "slug": "nova", "style": "@Gone"},  # renamed or deleted since
            {"element_id": uuid.uuid4(), "slug": "pip", "style": "@ink"},
            {"element_id": uuid.uuid4(), "slug": "kit", "style": "anime"},
        ]
    )
    out = await CinemaCastRecipe().resolve(None, uuid.uuid4(), inp, REG)
    assert [c.style for c in out.characters] == [None, "@ink", "anime"]
    assert await CinemaCastRecipe().resolve(None, uuid.uuid4(), out, REG) is out


async def test_a_mode_without_a_prompt_leaves_a_saved_style_out(monkeypatch):
    async def known(session, user_id, slugs):
        return []

    monkeypatch.setattr(generations, "unknown_styles", known)
    spec = REG.model("bytedance-upscaler")
    body = CreateRequest(
        model_id=spec.id, mode="upscale", prompt="", direction={"style": "@ink", "lighting": "golden_hour"}
    )
    assert await generations._saved_style(None, uuid.uuid4(), spec, body, "", False) == (
        "",
        {"lighting": "golden_hour"},
        [],
    )


class Rows(list):
    def scalars(self):
        return self

    def all(self):
        return list(self)


class Answers:
    """A session answering resolve_mentions' queries in order: the elements named, then their pictures."""

    def __init__(self, *answers):
        self.answers = list(answers)

    async def execute(self, _statement):
        return Rows(self.answers.pop(0))


async def test_a_description_ending_in_a_full_stop_reads_cleanly():
    juno = SimpleNamespace(
        id=uuid.uuid4(),
        slug="juno",
        name="Juno",
        description="a fox.",
        kind=ElementKind.character,
        voice={},
        lora={},
    )

    async def resolve(prompt: str) -> str:
        spec = REG.model("nano-banana-2")
        return (await resolve_mentions(Answers([juno], []), uuid.uuid4(), spec, "t2i", prompt, [])).prompt

    assert await resolve("@juno runs") == "Juno, a fox, runs"
    assert await resolve(with_style("a heron", "juno")) == "a heron. In the style of Juno, a fox."


def test_a_saved_style_is_named_at_the_end_of_the_prompt():
    assert style_slug("@Night-Ink") == "night-ink"
    assert style_slug("watercolor") is None and style_slug(None) is None and style_slug(3) is None
    assert with_style("a fox in the snow", "ink") == "a fox in the snow. In the style of @ink."
    assert with_style("A fox!  ", "ink") == "A fox! In the style of @ink."
    assert with_style("", "ink") == "In the style of @ink."


def test_enhance_leaves_a_saved_style_to_the_direction():
    text = build_request(REG.model("nano-banana-2"), "t2i", "a fox", ShotDirection(style="@night-ink"))
    assert "picture style (one of the user's saved styles)" in text


def test_a_picture_for_claude_is_a_jpeg_at_most_1280_on_its_long_side(tmp_path):
    big, small = tmp_path / "big.png", tmp_path / "small.webp"
    Image.new("RGBA", (2000, 1000), (200, 40, 40, 128)).save(big)
    Image.new("RGB", (600, 400), (40, 200, 40)).save(small)
    data, width, height = images.encode(big)
    assert (width, height) == (1280, 640) and images.tokens(width, height) == 46 * 23
    with Image.open(io.BytesIO(base64.b64decode(data))) as im:
        assert im.format == "JPEG" and im.size == (1280, 640)
    assert images.encode(small)[1:] == (600, 400) and images.tokens(600, 400) == 22 * 15


def test_a_cutout_is_seen_on_white_not_on_black(tmp_path):
    cutout = tmp_path / "cutout.png"
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(cutout)  # clear pixels that are black underneath
    with Image.open(io.BytesIO(base64.b64decode(images.encode(cutout)[0]))) as im:
        assert min(im.getpixel((32, 32))) > 245


def test_a_picture_too_large_to_decode_is_refused(tmp_path, monkeypatch):
    from studio.media import derive

    src = tmp_path / "p.png"
    Image.new("RGB", (400, 300)).save(src)
    monkeypatch.setattr(derive, "MAX_THUMB_SOURCE_PIXELS", 100_000)
    with pytest.raises(ValueError, match="too large"):
        images.encode(src)


async def test_a_clip_sends_its_middle_frame_and_leaves_no_file_behind(tmp_path, monkeypatch):
    picture, scratch = tmp_path / "p.png", tmp_path / "tmp"
    scratch.mkdir()
    Image.new("RGB", (512, 512), (10, 20, 30)).save(picture)
    files = {"p.png": picture, "clip.mp4": REPO / "fixtures" / "videos" / "clip_3s.mp4"}
    assets = [
        SimpleNamespace(
            id=uuid.uuid4(), kind=AssetKind.image, storage_key="p.png", duration_ms=None, title="p"
        ),
        SimpleNamespace(
            id=uuid.uuid4(), kind=AssetKind.video, storage_key="clip.mp4", duration_ms=3000, title="clip"
        ),
    ]

    async def load(session, user_id, ids):
        return assets

    monkeypatch.setattr(images, "load", load)
    monkeypatch.setattr(
        images, "get_storage", lambda: SimpleNamespace(path_for=files.__getitem__, temp_dir=lambda: scratch)
    )
    blocks, total = await images.image_blocks(None, uuid.uuid4(), [a.id for a in assets])
    assert [(b["type"], b["source"]["media_type"]) for b in blocks] == [("image", "image/jpeg")] * 2
    with Image.open(io.BytesIO(base64.b64decode(blocks[1]["source"]["data"]))) as frame:
        assert total == images.tokens(512, 512) + images.tokens(*frame.size)
    assert list(scratch.iterdir()) == []


def test_the_mock_answers_the_same_pictures_the_same_way():
    assert _mock("abc") == _mock("abc") and _mock("abc") is not _mock("abc")
    assert [_mock(h).name for h in ("d", "a", "b", "c")] == [m.name for m in MOCK_LOOKS]
    assert all(len(m.look) <= LOOK_LIMIT and len(m.name.split()) <= 3 for m in MOCK_LOOKS)


def test_a_long_look_is_cut_at_a_whole_cue():
    look = ", ".join(f"cue number {i}" for i in range(40))
    out = clip(look, LOOK_LIMIT, at_comma=True)
    assert len(out) <= LOOK_LIMIT and re.fullmatch(r"(cue number \d+, )*cue number \d+", out)
    words = clip("grain " * 60, LOOK_LIMIT, at_comma=True)
    assert len(words) <= LOOK_LIMIT and words.endswith("grain")
    assert clip("  short,  look, ", LOOK_LIMIT) == "short, look"
