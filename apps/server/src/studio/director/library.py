"""Director vocabulary (camera moves and cinema looks), loaded from registry/director/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from studio.config import get_settings


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


LOOK_SECTIONS = ("shot_sizes", "angles", "lenses", "focal_lengths", "apertures", "camera_bodies", "eras", "lighting",
                 "light_sources", "light_positions", "light_colors", "palettes", "genres", "pacing", "emotions")


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
    "genre": "genres", "pacing": "pacing", "emotion": "emotions",
}


def _load(directory: Path, stamp: tuple[float, ...]) -> DirectorLibrary:
    moves = yaml.safe_load((directory / "camera_moves.yaml").read_text(encoding="utf-8"))
    looks = yaml.safe_load((directory / "looks.yaml").read_text(encoding="utf-8"))
    lib = DirectorLibrary.model_validate({**moves, **looks})
    for section in ("moves", *LOOK_SECTIONS):
        ids = [i.id for i in getattr(lib, section)]
        if len(ids) != len(set(ids)):
            raise ValueError(f"director {section}: duplicate ids")
    cells = [(e.family, e.level) for e in lib.emotions]
    if len(cells) != len(set(cells)):
        raise ValueError("director emotions: two emotions share a wheel cell (family and level)")
    return lib


_cached = lru_cache(maxsize=4)(_load)


def load_library(directory: Path) -> DirectorLibrary:
    """The library in `directory`, re-read when one of its files changes."""
    stamp = tuple(p.stat().st_mtime for p in sorted(directory.glob("*.yaml")))
    return _cached(directory, stamp)


def get_library() -> DirectorLibrary:
    return load_library(get_settings().resolve_registry_dir() / "director")
