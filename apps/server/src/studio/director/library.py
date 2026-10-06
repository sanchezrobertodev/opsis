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


LOOK_SECTIONS = ("shot_sizes", "angles", "lenses", "apertures", "camera_bodies", "eras", "lighting", "palettes",
                 "genres", "pacing", "emotions")


class DirectorLibrary(_Strict):
    moves: list[CameraMove]
    shot_sizes: list[LookItem]
    angles: list[LookItem]
    lenses: list[LookItem]
    apertures: list[LookItem]
    camera_bodies: list[LookItem]
    eras: list[LookItem]
    lighting: list[LookItem]
    palettes: list[LookItem]
    genres: list[LookItem]
    pacing: list[LookItem]
    emotions: list[LookItem]

    def move(self, move_id: str) -> CameraMove | None:
        return next((m for m in self.moves if m.id == move_id), None)

    def look(self, section: str, item_id: str | None) -> LookItem | None:
        if not item_id:
            return None
        return next((i for i in getattr(self, section) if i.id == item_id), None)


# ShotDirection field -> DirectorLibrary section holding its options
FIELD_SECTIONS = {
    "shot_size": "shot_sizes", "angle": "angles", "lens": "lenses", "aperture": "apertures",
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
    return lib


_cached = lru_cache(maxsize=4)(_load)


def get_library() -> DirectorLibrary:
    directory = get_settings().resolve_registry_dir() / "director"
    stamp = tuple(p.stat().st_mtime for p in sorted(directory.glob("*.yaml")))
    return _cached(directory, stamp)
