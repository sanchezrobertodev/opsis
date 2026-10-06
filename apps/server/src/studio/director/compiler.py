"""The director: turns a prompt plus shot direction (camera moves, look) into the final prompt for one model.

Each model family reads prompts differently, so a dialect decides sentence shape and ordering:
Veo likes full cinematic sentences, Kling and Wan like comma-separated cues, Seedance likes labelled
lines, image models ignore camera motion. Pure functions only, so the output can be snapshot-tested and is
stored on every generation for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.director.library import DirectorLibrary, get_library
from studio.registry.schema import ModelSpec

MAX_MOVES = 3


class MoveChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    speed: Literal["slow", "normal", "fast"] = "normal"


class ShotDirection(BaseModel):
    """What the director panel sends. Every field is optional; empty means "let the model decide"."""

    model_config = ConfigDict(extra="forbid")
    subject: str | None = Field(None, description="Who or what the camera moves relative to")
    camera_moves: list[MoveChoice] = Field(default_factory=list, max_length=MAX_MOVES)
    shot_size: str | None = None
    angle: str | None = None
    lens: str | None = None
    aperture: str | None = None
    camera_body: str | None = None
    era: str | None = None
    lighting: str | None = None
    palette: str | None = None
    genre: str | None = None
    pacing: str | None = None
    emotion: str | None = None

    def is_empty(self) -> bool:
        return not self.camera_moves and not any(
            getattr(self, f) for f in ("shot_size", "angle", "lens", "aperture", "camera_body", "era", "lighting",
                                       "palette", "genre", "pacing", "emotion"))


@dataclass
class Compiled:
    prompt: str
    warnings: list[str] = field(default_factory=list)
    native_params: dict[str, Any] = field(default_factory=dict)
    dialect: str = "default"

    def as_dict(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "warnings": self.warnings, "native_params": self.native_params,
                "dialect": self.dialect}


@dataclass
class Parts:
    moves: list[str]
    framing: list[str]  # shot size, angle
    optics: list[str]  # lens, aperture, camera body
    light: list[str]  # lighting, palette
    mood: list[str]  # genre, era, pacing, emotion


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _join_moves(moves: list[str]) -> str:
    return ", then ".join(moves)


def _phrase_moves(lib: DirectorLibrary, d: ShotDirection, dialect: str, subject: str, warnings: list[str]) -> list[str]:
    out = []
    for choice in d.camera_moves:
        move = lib.move(choice.id)
        if move is None:
            warnings.append(f"unknown camera move {choice.id!r} ignored")
            continue
        phrase = move.dialects.get(dialect, move.prompt)
        if not phrase:
            continue  # "let the model decide"
        phrase = phrase.replace("{subject}", subject)
        if move.speeds and choice.speed == "slow":
            phrase += ", slow and smooth"
        elif move.speeds and choice.speed == "fast":
            phrase += ", fast"
        out.append(phrase)
    return out


def _look(lib: DirectorLibrary, section: str, item_id: str | None, subject: str, warnings: list[str]) -> list[str]:
    if not item_id:
        return []
    item = lib.look(section, item_id)
    if item is None:
        warnings.append(f"unknown {section.rstrip('s').replace('_', ' ')} {item_id!r} ignored")
        return []
    return [item.prompt.replace("{subject}", subject)]


def _render(dialect: str, prompt: str, p: Parts, is_image: bool) -> str:
    prompt = prompt.strip()
    if dialect in ("kling", "wan"):
        cues = [*p.framing, *([f"camera: {_join_moves(p.moves)}"] if p.moves else []), *p.optics, *p.light, *p.mood]
        return ", ".join(x for x in [prompt.rstrip(".,"), *cues] if x)
    if dialect == "seedance":
        lines = [_sentence(prompt)]
        if p.moves:
            lines.append(f"Camera: {_join_moves(p.moves)}.")
        if p.framing or p.optics:
            lines.append(f"Shot: {', '.join([*p.framing, *p.optics])}.")
        if p.light:
            lines.append(f"Look: {', '.join(p.light)}.")
        if p.mood:
            lines.append(f"Mood: {', '.join(p.mood)}.")
        return "\n".join(lines)
    # veo, image models and the default: full sentences, subject first, then composition and style.
    sentences = [_sentence(prompt)]
    if p.framing:
        sentences.append(_sentence(", ".join(p.framing)))
    if p.moves and not is_image:
        moves = _join_moves(p.moves)
        sentences.append(_sentence(moves if moves.lower().startswith("the camera") else f"The camera moves: {moves}"))
    if p.optics:
        sentences.append(_sentence(", ".join(p.optics)))
    if p.light:
        sentences.append(_sentence(", ".join(p.light)))
    if p.mood:
        sentences.append(_sentence(", ".join(p.mood)))
    return " ".join(s for s in sentences if s)


def compile_prompt(spec: ModelSpec, prompt: str, direction: ShotDirection | None,
                   lib: DirectorLibrary | None = None) -> Compiled:
    dialect = spec.prompt.dialect
    if direction is None or direction.is_empty():
        return Compiled(prompt=prompt.strip(), dialect=dialect)
    lib = lib or get_library()
    warnings: list[str] = []
    subject = (direction.subject or "").strip() or "the subject"
    is_image = spec.output_type == "image"

    moves = _phrase_moves(lib, direction, dialect, subject, warnings)
    if is_image and moves:
        warnings.append("camera moves don't apply to still images and were left out")
        moves = []
    native: dict[str, Any] = {}
    native_key = (spec.camera or {}).get("native")
    if native_key and direction.camera_moves:
        first = lib.move(direction.camera_moves[0].id)
        if first and native_key in first.native:
            native = {"camera_control": first.native[native_key]}

    parts = Parts(
        moves=moves,
        framing=_look(lib, "shot_sizes", direction.shot_size, subject, warnings)
        + _look(lib, "angles", direction.angle, subject, warnings),
        optics=_look(lib, "lenses", direction.lens, subject, warnings)
        + _look(lib, "apertures", direction.aperture, subject, warnings)
        + _look(lib, "camera_bodies", direction.camera_body, subject, warnings),
        light=_look(lib, "lighting", direction.lighting, subject, warnings)
        + _look(lib, "palettes", direction.palette, subject, warnings),
        mood=_look(lib, "genres", direction.genre, subject, warnings)
        + _look(lib, "eras", direction.era, subject, warnings)
        + _look(lib, "pacing", direction.pacing, subject, warnings)
        + _look(lib, "emotions", direction.emotion, subject, warnings),
    )
    text = _render(dialect, prompt, parts, is_image)

    # Over the model's limit: drop the softest cues first, never the user's prompt or camera moves.
    limit = spec.prompt.max_chars
    for bucket in ("mood", "light", "optics", "framing"):
        if len(text) <= limit:
            break
        if getattr(parts, bucket):
            setattr(parts, bucket, [])
            warnings.append(f"dropped {bucket} cues to fit the {limit}-character limit")
            text = _render(dialect, prompt, parts, is_image)
    if len(text) > limit:
        warnings.append(f"prompt is still {len(text)} characters; the model allows {limit}")
    return Compiled(prompt=text, warnings=warnings, native_params=native, dialect=dialect)
