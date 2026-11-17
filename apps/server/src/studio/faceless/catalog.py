"""Faceless Studio themes (registry/faceless/themes.yaml, reloaded when it changes) and styles: the picture styles of
registry/styles/ that have a caption look, read from the director library on every call so its reloads show."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from studio.config import get_settings
from studio.director.library import CaptionLook, get_library  # CaptionLook stays importable from here


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Theme(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    label: str
    summary: str
    script_guidance: str
    voice_id: str
    music: str


class Style(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    label: str
    summary: str
    prompt: str
    caption: CaptionLook = CaptionLook()


class FacelessCatalog(_Strict):
    themes: list[Theme]

    @property
    def styles(self) -> list[Style]:
        """The library's styles with a caption look, by category then label (the library's order)."""
        return [Style(id=s.id, label=s.label, summary=s.summary, prompt=s.prompt, caption=s.caption)
                for s in get_library().styles if s.caption]

    def theme(self, theme_id: str) -> Theme | None:
        return next((t for t in self.themes if t.id == theme_id), None)

    def style(self, style_id: str) -> Style | None:
        return next((s for s in self.styles if s.id == style_id), None)


def _load(directory: Path, stamp: tuple[float, ...]) -> FacelessCatalog:
    catalog = FacelessCatalog.model_validate(yaml.safe_load((directory / "themes.yaml").read_text(encoding="utf-8")))
    ids = [t.id for t in catalog.themes]
    if len(ids) != len(set(ids)):
        raise ValueError("faceless themes: duplicate ids")
    return catalog


_cached = lru_cache(maxsize=4)(_load)


def get_catalog() -> FacelessCatalog:
    directory = get_settings().resolve_registry_dir() / "faceless"
    stamp = tuple(p.stat().st_mtime for p in sorted(directory.glob("*.yaml")))
    return _cached(directory, stamp)
