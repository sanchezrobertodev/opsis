"""Faceless Studio themes and styles, loaded from registry/faceless/*.yaml (reloaded when the files change)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from studio.config import get_settings

HEX = r"^#[0-9A-Fa-f]{6}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptionLook(_Strict):
    font: str = "Arial"
    size: int = Field(58, ge=24, le=120, description="Font size for a 1080-pixel-tall frame")
    bold: bool = True
    color: str = Field("#FFFFFF", pattern=HEX)
    outline: str = Field("#000000", pattern=HEX)
    highlight: str = Field("#FFD54A", pattern=HEX, description="The word being spoken")
    uppercase: bool = False
    position: str = Field("bottom", pattern="^(bottom|middle|top)$")
    max_chars: int = Field(30, ge=12, le=60, description="Characters per caption line")


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
    styles: list[Style]

    def theme(self, theme_id: str) -> Theme | None:
        return next((t for t in self.themes if t.id == theme_id), None)

    def style(self, style_id: str) -> Style | None:
        return next((s for s in self.styles if s.id == style_id), None)


def _load(directory: Path, stamp: tuple[float, ...]) -> FacelessCatalog:
    data: dict = {}
    for name in ("themes.yaml", "styles.yaml"):
        data.update(yaml.safe_load((directory / name).read_text(encoding="utf-8")) or {})
    catalog = FacelessCatalog.model_validate(data)
    for section in ("themes", "styles"):
        ids = [i.id for i in getattr(catalog, section)]
        if len(ids) != len(set(ids)):
            raise ValueError(f"faceless {section}: duplicate ids")
    for style in catalog.styles:
        if re.search(r"\b(text|caption|logo|watermark)s?\b", style.prompt, re.I):
            raise ValueError(f"faceless style {style.id}: keep words like 'text' out of the look (models render it)")
    return catalog


_cached = lru_cache(maxsize=4)(_load)


def get_catalog() -> FacelessCatalog:
    directory = get_settings().resolve_registry_dir() / "faceless"
    stamp = tuple(p.stat().st_mtime for p in sorted(directory.glob("*.yaml")))
    return _cached(directory, stamp)
