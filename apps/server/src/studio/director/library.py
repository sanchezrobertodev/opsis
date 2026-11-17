"""Director vocabulary (camera moves, cinema looks and picture styles), loaded from registry/director/*.yaml and
registry/styles/<category>/<id>.yaml."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from studio.config import get_settings

log = logging.getLogger(__name__)

HEX = r"^#[0-9A-Fa-f]{6}$"
LETTERING = re.compile(r"\b(text|caption|logo|watermark)s?\b", re.I)  # words that make models draw lettering


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraMove(_Strict):
    id: str
    label: str
    category: Literal["push", "orbit", "lateral", "vertical", "handheld", "vehicle", "time", "lens", "static"]
    prompt: str
    description: str = ""
    speeds: bool = False
    dialects: dict[str, str] = {}
    native: dict[str, Any] = {}
    glyph: str = ""  # how the picker's little preview moves (web `glyph-*` classes); empty = the category's


class LookItem(_Strict):
    id: str
    label: str
    prompt: str
    dialects: dict[str, str] = {}  # a phrase for one model family instead of `prompt`
    swatch: list[str] = []  # hex colors the picker shows (palettes, light colors)

    def phrase(self, dialect: str) -> str:
        return self.dialects.get(dialect, self.prompt)


EmotionFamily = Literal["joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation"]


class EmotionItem(LookItem):
    """One cell of the emotion wheel: a family and how strongly it shows (1 mild, 2 plain, 3 intense)."""

    family: EmotionFamily
    level: Literal[1, 2, 3]


class CaptionLook(_Strict):
    """How Faceless Studio's burned-in captions look. Fonts are ones Windows ships (infra/server/fonts.conf maps them
    to open stand-ins on a Linux server)."""

    font: str = "Arial"
    size: int = Field(58, ge=24, le=120, description="Font size for a 1080-pixel-tall frame")
    bold: bool = True
    color: str = Field("#FFFFFF", pattern=HEX)
    outline: str = Field("#000000", pattern=HEX)
    highlight: str = Field("#FFD54A", pattern=HEX, description="The word being spoken")
    uppercase: bool = False
    position: str = Field("bottom", pattern="^(bottom|middle|top)$")
    max_chars: int = Field(30, ge=12, le=60, description="Characters per caption line")


StyleCategory = Literal["photo", "illustration", "3d", "painting", "retro"]
STYLE_CATEGORIES: tuple[str, ...] = get_args(StyleCategory)  # the order galleries show them in


class StyleItem(LookItem):
    """A picture style, shared by Create image, the character designer and Faceless Studio."""

    id: str = Field(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")  # the web's designerStyle() passes these through
    category: StyleCategory
    summary: str
    photographic: bool = Field(False, description="A photograph or a photographed object: lens and camera apply")
    caption: CaptionLook | None = Field(None, description="Faceless Studio offers the styles that have one")


LOOK_SECTIONS = ("shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
                 "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions",
                 "styles")


class DirectorLibrary(_Strict):
    moves: list[CameraMove]
    shot_sizes: list[LookItem]
    angles: list[LookItem]
    lenses: list[LookItem]
    focal_lengths: list[LookItem]
    apertures: list[LookItem]
    camera_bodies: list[LookItem]
    eras: list[LookItem]
    lighting: list[LookItem]
    light_sources: list[LookItem]
    light_positions: list[LookItem]
    light_colors: list[LookItem]
    palettes: list[LookItem]
    genres: list[LookItem]
    pacing: list[LookItem]
    emotions: list[EmotionItem]
    styles: list[StyleItem]

    def move(self, move_id: str) -> CameraMove | None:
        return next((m for m in self.moves if m.id == move_id), None)

    def look(self, section: str, item_id: str | None) -> LookItem | None:
        if not item_id:
            return None
        return next((i for i in getattr(self, section) if i.id == item_id), None)


# ShotDirection field -> DirectorLibrary section holding its options
FIELD_SECTIONS = {
    "shot_size": "shot_sizes", "angle": "angles", "lens": "lenses", "focal_length": "focal_lengths",
    "aperture": "apertures",
    "camera_body": "camera_bodies", "era": "eras", "lighting": "lighting", "palette": "palettes",
    "genre": "genres", "pacing": "pacing", "emotion": "emotions", "style": "styles",
}


def _read(path: Path, root: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # a file mid-edit
        raise ValueError(f"{path.relative_to(root).as_posix()}: not valid YAML ({exc})") from exc


def _style(path: Path, root: Path) -> StyleItem:
    """One styles/<category>/<id>.yaml: its id is the file's name and its category the folder's."""
    where = path.relative_to(root).as_posix()
    try:
        style = StyleItem.model_validate(_read(path, root))
    except ValidationError as exc:
        raise ValueError(f"{where}: {exc}") from exc
    if style.id != path.stem:
        raise ValueError(f"{where}: id {style.id!r} must be the file's name ({path.stem})")
    if style.category != path.parent.name:
        raise ValueError(f"{where}: category {style.category!r} must be its folder's name ({path.parent.name})")
    if any(LETTERING.search(text) for text in (style.prompt, *style.dialects.values())):
        raise ValueError(f"{where}: keep words like 'text' out of the prompt (models draw them)")
    return style


def _load(root: Path, stamp: tuple) -> DirectorLibrary:
    data: dict[str, Any] = {}
    for name in ("camera_moves.yaml", "looks.yaml"):
        data.update(_read(root / "director" / name, root) or {})
    # every file at any depth, so one outside its category folder is refused rather than never read
    styles = [_style(p, root) for p in sorted((root / "styles").rglob("*.yaml"))]
    data["styles"] = sorted(styles, key=lambda s: (STYLE_CATEGORIES.index(s.category), s.label.casefold()))
    lib = DirectorLibrary.model_validate(data)
    for section in ("moves", *LOOK_SECTIONS):
        ids = [i.id for i in getattr(lib, section)]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"director {section}: duplicate ids {dupes}")
    cells = [(e.family, e.level) for e in lib.emotions]
    if len(cells) != len(set(cells)):
        raise ValueError("director emotions: two emotions share a wheel cell (family and level)")
    return lib


_cached = lru_cache(maxsize=4)(_load)


def _stamp(root: Path) -> tuple[tuple[str, int, int], ...]:
    """Each vocabulary and style file with its time and size, so an edited, added, moved or removed file shows."""
    paths = sorted([*(root / "director").glob("*.yaml"), *(root / "styles").rglob("*.yaml")])
    return tuple((p.as_posix(), (st := p.stat()).st_mtime_ns, st.st_size) for p in paths)


def load_library(root: Path) -> DirectorLibrary:
    """The vocabulary in the registry folder `root`, re-read when one of its files changes. A broken file raises."""
    return _cached(root, _stamp(root))


_good: dict[Path, DirectorLibrary] = {}  # per registry folder: the last library that loaded
_tried: dict[Path, tuple] = {}  # and the files last tried, so a broken change is read and logged once


def get_library() -> DirectorLibrary:
    """The studio's vocabulary. A change that breaks it keeps the last good library (the registry reports the error,
    `load_registry`); it raises only when none has loaded yet."""
    root = get_settings().resolve_registry_dir()
    try:
        stamp = _stamp(root)
        if stamp != _tried.get(root):
            _tried[root] = stamp
            _good[root] = _cached(root, stamp)
    except Exception as exc:  # a file mid-edit or a mistake: keep working with what loaded last
        if root not in _good:
            _tried.pop(root, None)
            raise
        log.error("director library didn't load, keeping the last good one: %s", exc)
    return _good[root]
