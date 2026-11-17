"""The character designer's vocabulary and starter characters (registry/characters/*.yaml), re-read when a file, the
styles or the registry change. A change that breaks them keeps the last good catalog (logged once, and the registry
reports it, `load_registry`); it raises only when none has loaded."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from studio.config import get_settings
from studio.director.library import get_library
from studio.registry.loader import get_registry
from studio.registry.schema import ModelSpec

log = logging.getLogger(__name__)

# the presentation with a creature's own traits (web lib/designer.ts:CREATURE)
CREATURE = "non-human creature"
# whose outfits and pictures show with no presentation picked (web lib/designer.ts:optionsFor, traitPicture)
DEFAULT_SET = "man"
# never a trait's key: presentation and style sit beside the traits in `attributes.traits`, the name in a draft
RESERVED = {"presentation", "style", "name"}
PLACEHOLDERS = {"o", "a"}


def slug(option: str) -> str:
    """An option's (or presentation's) name in picture files, like the web's `traitFile`."""
    return re.sub(r"[^a-z0-9]+", "-", option.lower()).strip("-")


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w*)\}", text))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterTrait(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z_]*$")
    label: str
    options: list[str] = Field(min_length=1, description="Shown for every presentation")
    multi: bool = Field(False, description="Several picks, saved as one string joined with ', '")
    by_presentation: dict[str, list[str]] = Field(
        {},
        description="A presentation's own options, shown after the shared ones (the man's with none picked)",
    )
    phrase: str = Field(
        "",
        description="How a pick reads in a description: {o} is the option, {a} the option after "
        "a/an. Empty: worded together with other traits in code",
    )
    overrides: dict[str, str] = Field({}, description="The wording of single options, instead of the phrase")

    def options_for(self, presentation: str | None) -> list[str]:
        return [*self.options, *self.by_presentation.get(presentation or DEFAULT_SET, [])]

    @property
    def every_option(self) -> list[str]:
        return list(
            dict.fromkeys([*self.options, *(o for own in self.by_presentation.values() for o in own)])
        )

    @model_validator(mode="after")
    def _check(self) -> CharacterTrait:
        if bad := [o for o in self.every_option if not o or o != o.strip() or "," in o]:
            raise ValueError(f"{self.key}: options must be trimmed words without commas, not {bad}")
        for who in {None, *self.by_presentation}:
            shown = self.options_for(who)
            if dupes := sorted({o for o in shown if shown.count(o) > 1}):
                raise ValueError(f"{self.key}: options {dupes} repeated")
        if self.phrase and not _placeholders(self.phrase):
            raise ValueError(f"{self.key}: phrase {self.phrase!r} needs {{o}} or {{a}}")
        if bad := [t for t in (self.phrase, *self.overrides.values()) if _placeholders(t) - PLACEHOLDERS]:
            raise ValueError(f"{self.key}: {bad} take no placeholders but {{o}} and {{a}}")
        if unknown := sorted(set(self.overrides) - set(self.every_option)):
            raise ValueError(f"{self.key}: overrides for options it doesn't have: {unknown}")
        return self


class StarterCharacter(_Strict):
    id: str = Field(
        pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$",
        description="Its portrait is apps/web/public/starters/characters/<id>.webp",
    )
    name: str = Field(min_length=1, max_length=80)
    summary: str
    traits: dict[str, str] = Field(
        description="Designer picks as saved in attributes.traits, presentation included"
    )
    style: str = Field(description="A catalog style id")
    voice_id: str = Field(description="A voice_id option of the catalog's voice_model")


class CharacterCatalog(_Strict):
    presentations: list[str] = Field(
        description=f"{CREATURE!r} takes the creature traits, the others a person's"
    )
    person: list[CharacterTrait]
    creature: list[CharacterTrait]
    voice_model: str = Field(description="The model the starters' voices are from")
    starters: list[StarterCharacter] = Field(min_length=1)

    def traits_for(self, presentation: str | None) -> list[CharacterTrait]:
        return self.creature if presentation == CREATURE else self.person

    @model_validator(mode="after")
    def _check(self) -> CharacterCatalog:
        if CREATURE not in self.presentations or len(set(self.presentations)) != len(self.presentations):
            raise ValueError(f"presentations must be different and include {CREATURE!r}")
        people = set(self.presentations) - {CREATURE}
        for kind, traits in (("person", self.person), ("creature", self.creature)):
            keys = [t.key for t in traits]
            if bad := sorted({k for k in keys if keys.count(k) > 1} | (set(keys) & RESERVED)):
                raise ValueError(f"{kind} traits: {bad} can't be keys (repeated or reserved)")
            for t in traits:
                if unknown := sorted(set(t.by_presentation) - (people if kind == "person" else set())):
                    raise ValueError(
                        f"{kind} trait {t.key}: options for {unknown}, which take no {kind} traits"
                    )
        multi = {t.key: t.multi for t in self.person}
        if mixed := sorted(t.key for t in self.creature if multi.get(t.key, t.multi) != t.multi):
            raise ValueError(f"{mixed}: a key both kinds have takes one pick in both, or several in both")
        ids = [s.id for s in self.starters]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"starters: duplicate ids {dupes}")
        for starter in self.starters:
            self._check_picks(starter)
        return self

    def _check_picks(self, starter: StarterCharacter) -> None:
        where = f"starter {starter.id}"
        presentation = starter.traits.get("presentation")
        if presentation not in self.presentations:
            raise ValueError(f"{where}: presentation {presentation!r} isn't one of {self.presentations}")
        if "style" in starter.traits:
            raise ValueError(f"{where}: the style goes in `style`, not in its traits")
        traits = {t.key: t for t in self.traits_for(presentation)}
        for key, value in starter.traits.items():
            if key == "presentation":
                continue
            if (t := traits.get(key)) is None:
                raise ValueError(f"{where}: a {presentation} has no trait {key!r}")
            allowed = t.options_for(presentation)
            if bad := [p for p in (value.split(", ") if t.multi else [value]) if p not in allowed]:
                raise ValueError(
                    f"{where}: {key} {', '.join(bad)!r} isn't an option for a {presentation} "
                    f"({', '.join(allowed)})"
                )


def _read(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # a file mid-edit
        raise ValueError(f"characters/{path.name}: not valid YAML ({exc})") from exc


def read_characters(root: Path, styles: Collection[str], models: Mapping[str, ModelSpec]) -> CharacterCatalog:
    """The vocabulary and starters in the registry folder `root`, each starter's style one of `styles` (catalog ids)
    and its voice one of the voice model's in `models`. A broken file raises ValueError, saying which and why."""
    folder = root / "characters"
    try:
        catalog = CharacterCatalog.model_validate(
            {**_read(folder / "traits.yaml"), **_read(folder / "starters.yaml")}
        )
    except ValidationError as exc:
        raise ValueError(f"characters: {exc}") from exc
    voice = models.get(catalog.voice_model)
    param = voice.param("voice_id") if voice else None
    voices = {str(o) for o in getattr(param, "options", None) or []}
    for starter in catalog.starters:
        if starter.style not in styles:
            raise ValueError(
                f"characters/starters.yaml: starter {starter.id}: unknown style {starter.style!r}"
            )
        if starter.voice_id not in voices:
            raise ValueError(
                f"characters/starters.yaml: starter {starter.id}: {starter.voice_id!r} isn't one of "
                f"{catalog.voice_model}'s voices"
            )
    return catalog


def _load(root: Path, key: tuple) -> CharacterCatalog:
    # checked against the studio's own styles and voices
    return read_characters(root, {s.id for s in get_library().styles}, get_registry().models)


_cached = lru_cache(maxsize=4)(_load)


def _key(root: Path) -> tuple:
    """Each file with its time and size, so an edited, added or removed file shows, and what the starters are checked
    against (the style ids, the registry by its hash), so a style or voice that comes or goes checks them again."""
    files = tuple(
        (p.as_posix(), (st := p.stat()).st_mtime_ns, st.st_size)
        for p in sorted((root / "characters").glob("*.yaml"))
    )
    return files, tuple(s.id for s in get_library().styles), get_registry().hash


def load_characters(root: Path) -> CharacterCatalog:
    """The vocabulary and starters in the registry folder `root`. A broken file raises."""
    return _cached(root, _key(root))


_good: dict[Path, CharacterCatalog] = {}  # per registry folder: the last catalog that loaded
_tried: dict[Path, tuple] = {}  # and what it was last tried with, so a broken change is read and logged once


def get_characters() -> CharacterCatalog:
    root = get_settings().resolve_registry_dir()
    try:
        key = _key(root)
        if key != _tried.get(root):
            _tried[root] = key
            _good[root] = _cached(root, key)
    except Exception as exc:  # a file mid-edit or a mistake: keep working with what loaded last
        if root not in _good:
            _tried.pop(root, None)
            raise
        log.error("character vocabulary didn't load, keeping the last good one: %s", exc)
    return _good[root]
