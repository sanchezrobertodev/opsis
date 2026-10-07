"""The director: turns a prompt plus shot direction (camera moves, look) into the final prompt for one model.

Each model family reads prompts differently, so a dialect decides sentence shape and ordering:
Veo likes full cinematic sentences, Kling and Wan like comma-separated cues, Seedance likes labelled
lines, image models ignore camera motion. Pure functions only, so the output can be snapshot-tested and is
stored on every generation for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.director.library import DirectorLibrary, get_library
from studio.registry.schema import ModelSpec

MAX_MOVES = 3
MAX_LIGHTS = 2
MAX_CAST_EMOTIONS = 6
MAX_LINES = 12
# ShotDirection fields that each pick one item from a look list (studio.director.library.FIELD_SECTIONS)
LOOK_FIELDS = ("shot_size", "angle", "lens", "focal_length", "aperture", "camera_body", "era", "lighting", "palette",
               "genre", "pacing", "emotion")


class MoveChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    speed: Literal["slow", "normal", "fast"] = "normal"


class LightChoice(BaseModel):
    """One source on the lighting console."""

    model_config = ConfigDict(extra="forbid")
    source: str = Field(description="A light_sources id")
    position: str = Field("front_left", description="A light_positions id")
    color: str | None = Field(None, description="A light_colors id; none keeps the source's own color")
    quality: Literal["soft", "hard"] = "soft"
    intensity: Literal["dim", "normal", "bright"] = "normal"


class CastEmotion(BaseModel):
    """How one cast member feels in the shot, from the emotion wheel."""

    model_config = ConfigDict(extra="forbid")
    who: str = Field(min_length=1, max_length=60, description="An @mention of a cast member, or a plain name")
    emotion: str = Field(description="An emotions id")


class DialogueLine(BaseModel):
    """One spoken line: who says it (an @mention of a cast member, or a name) and the words."""

    model_config = ConfigDict(extra="forbid")
    who: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=500)
    voice_id: str | None = Field(None, description="The voice to speak it with, when it's voiced separately")


class ShotDirection(BaseModel):
    """What the director panel sends. Every field is optional; empty means "let the model decide"."""

    model_config = ConfigDict(extra="forbid")
    subject: str | None = Field(None, description="Who or what the camera moves relative to")
    camera_moves: list[MoveChoice] = Field(default_factory=list, max_length=MAX_MOVES)
    shot_size: str | None = None
    angle: str | None = None
    lens: str | None = None
    focal_length: str | None = None
    aperture: str | None = None
    camera_body: str | None = None
    era: str | None = None
    lighting: str | None = None
    lights: list[LightChoice] = Field(default_factory=list, max_length=MAX_LIGHTS,
                                      description="The lighting console: a key light and an optional second light")
    palette: str | None = None
    genre: str | None = None
    pacing: str | None = None
    emotion: str | None = Field(None, description="How the subject feels")
    cast_emotions: list[CastEmotion] = Field(default_factory=list, max_length=MAX_CAST_EMOTIONS,
                                             description="How each @mentioned cast member feels")
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=MAX_LINES,
                                         description="Lines spoken in the shot, in order")

    @model_validator(mode="before")
    @classmethod
    def _focal_lengths_moved(cls, data: Any) -> Any:
        """Focal lengths used to be lenses (`lens: wide_24`); such a value moves to `focal_length`."""
        if isinstance(data, dict) and (lens := data.get("lens")) and not data.get("focal_length"):
            lib = get_library()
            if lib.look("lenses", lens) is None and lib.look("focal_lengths", lens) is not None:
                data = {**data, "lens": None, "focal_length": lens}
        return data

    @model_validator(mode="after")
    def _one_emotion_each(self) -> ShotDirection:
        who = [c.who.strip().lstrip("@").lower() for c in self.cast_emotions]
        if len(who) != len(set(who)):
            raise ValueError("one emotion per cast member")
        return self

    def is_empty(self) -> bool:
        return not (self.camera_moves or self.lights or self.cast_emotions or self.dialogue) and not any(
            getattr(self, f) for f in LOOK_FIELDS)


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
    optics: list[str]  # lens, focal length, aperture, camera body
    light: list[str]  # lighting preset, palette
    mood: list[str]  # genre, era, pacing
    lights: list[str] = field(default_factory=list)  # the lighting console
    performance: list[str] = field(default_factory=list)  # the subject's and each cast member's emotion
    speech: list[str] = field(default_factory=list)  # dialogue, for models that voice it; never shed


# What goes first when a prompt is over the model's limit: the softest cues. Never the prompt or camera moves.
SHED_ORDER = (("mood",), ("light", "lights"), ("optics",), ("performance",), ("framing",))


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


def _item_name(section: str) -> str:
    irregular = {"lenses": "lens", "camera_bodies": "camera body", "lighting": "lighting", "pacing": "pacing"}
    return irregular.get(section, section.rstrip("s")).replace("_", " ")


def _look(lib: DirectorLibrary, section: str, item_id: str | None, subject: str, warnings: list[str],
          dialect: str = "default") -> list[str]:
    if not item_id:
        return []
    item = lib.look(section, item_id)
    if item is None:
        warnings.append(f"unknown {_item_name(section)} {item_id!r} ignored")
        return []
    return [item.phrase(dialect).replace("{subject}", subject)]


def _phrase_lights(lib: DirectorLibrary, d: ShotDirection, dialect: str, subject: str,
                   warnings: list[str]) -> list[str]:
    """Each console light as "key light: hard warm tungsten spotlight from the left"."""
    out: list[str] = []
    for light in d.lights:
        source = lib.look("light_sources", light.source)
        if source is None:
            warnings.append(f"unknown light source {light.source!r} ignored")
            continue
        color = lib.look("light_colors", light.color) if light.color else None
        if light.color and color is None:
            warnings.append(f"unknown light color {light.color!r} ignored")
        position = lib.look("light_positions", light.position)
        if position is None:
            warnings.append(f"unknown light position {light.position!r} ignored")
        words = [{"dim": "dim", "bright": "bright"}.get(light.intensity), light.quality,
                 color.phrase(dialect) if color else None, source.phrase(dialect),
                 position.phrase(dialect).replace("{subject}", subject) if position else None]
        out.append(f"{'second light' if out else 'key light'}: {' '.join(w for w in words if w)}")
    return out


def _phrase_cast(lib: DirectorLibrary, d: ShotDirection, dialect: str, names: dict[str, str] | None,
                 warnings: list[str]) -> list[str]:
    """Each cast member's emotion, naming them the way the prompt does (`names`: @slug -> resolved name)."""
    out = []
    for ce in d.cast_emotions:
        item = lib.look("emotions", ce.emotion)
        if item is None:
            warnings.append(f"unknown emotion {ce.emotion!r} for {ce.who} ignored")
            continue
        who = ce.who.strip()
        if who.startswith("@") and names is not None:
            slug = who[1:].lower()
            if slug not in names:
                warnings.append(f"{who} isn't in this shot, so their emotion was left out")
                continue
            who = names[slug]
        out.append(item.phrase(dialect).replace("{subject}", who))
    return out


def _speaker(who: str, names: dict[str, str] | None) -> str:
    """A line's speaker as the prompt names them: an @mention resolved like the prompt's, else the name as written."""
    who = who.strip()
    if who.startswith("@"):
        return (names or {}).get(who[1:].lower(), who[1:])
    return who


def _phrase_speech(d: ShotDirection, names: dict[str, str] | None) -> list[str]:
    return [f'{_speaker(line.who, names)} says: "{line.text.strip()}"' for line in d.dialogue]


def unknown_ids(d: ShotDirection, lib: DirectorLibrary | None = None) -> list[str]:
    """Every id in a direction that the director library doesn't know (for linting presets)."""
    from studio.director.library import FIELD_SECTIONS

    lib = lib or get_library()
    out = [f"unknown camera move {m.id!r}" for m in d.camera_moves if lib.move(m.id) is None]
    out += [f"unknown {name.replace('_', ' ')} {value!r}" for name, section in FIELD_SECTIONS.items()
            if (value := getattr(d, name)) is not None and lib.look(section, value) is None]
    for light in d.lights:
        out += [f"unknown light {what} {value!r}" for what, section, value in (
            ("source", "light_sources", light.source), ("position", "light_positions", light.position),
            ("color", "light_colors", light.color)) if value is not None and lib.look(section, value) is None]
    out += [f"unknown emotion {c.emotion!r}" for c in d.cast_emotions if lib.look("emotions", c.emotion) is None]
    return out


def _render(dialect: str, prompt: str, p: Parts, is_image: bool) -> str:
    prompt = prompt.strip()
    if dialect in ("kling", "wan"):
        cues = [*p.framing, *p.performance, *p.speech, *([f"camera: {_join_moves(p.moves)}"] if p.moves else []), *p.optics,
                *p.lights, *p.light, *p.mood]
        return ", ".join(x for x in [prompt.rstrip(".,"), *cues] if x)
    if dialect == "seedance":
        lines = [_sentence(prompt)]
        if p.moves:
            lines.append(f"Camera: {_join_moves(p.moves)}.")
        if p.framing or p.optics:
            lines.append(f"Shot: {', '.join([*p.framing, *p.optics])}.")
        if p.performance:
            lines.append(f"Performance: {'; '.join(p.performance)}.")
        if p.speech:
            lines.append(f"Dialogue: {' '.join(p.speech)}")
        lines += [_sentence(x) for x in p.lights]
        if p.light:
            lines.append(f"Look: {', '.join(p.light)}.")
        if p.mood:
            lines.append(f"Mood: {', '.join(p.mood)}.")
        return "\n".join(lines)
    # veo, image models and the default: full sentences, subject first, then composition and style.
    sentences = [_sentence(prompt)]
    if p.framing:
        sentences.append(_sentence(", ".join(p.framing)))
    sentences += [_sentence(x) for x in p.performance]
    sentences += p.speech
    if p.moves and not is_image:
        moves = _join_moves(p.moves)
        sentences.append(_sentence(moves if moves.lower().startswith("the camera") else f"The camera moves: {moves}"))
    if p.optics:
        sentences.append(_sentence(", ".join(p.optics)))
    sentences += [_sentence(x) for x in p.lights]
    if p.light:
        sentences.append(_sentence(", ".join(p.light)))
    if p.mood:
        sentences.append(_sentence(", ".join(p.mood)))
    return " ".join(s for s in sentences if s)


def compile_prompt(spec: ModelSpec, prompt: str, direction: ShotDirection | None,
                   lib: DirectorLibrary | None = None, names: dict[str, str] | None = None) -> Compiled:
    """`names` maps each @mentioned slug in the prompt to how the prompt now names it (after @mention resolution),
    so a cast member's emotion refers to them the same way; None leaves those mentions as written."""
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

    def look(section: str, item_id: str | None) -> list[str]:
        return _look(lib, section, item_id, subject, warnings, dialect)

    parts = Parts(
        moves=moves,
        framing=look("shot_sizes", direction.shot_size) + look("angles", direction.angle),
        optics=look("lenses", direction.lens) + look("focal_lengths", direction.focal_length)
        + look("apertures", direction.aperture) + look("camera_bodies", direction.camera_body),
        light=look("lighting", direction.lighting) + look("palettes", direction.palette),
        mood=look("genres", direction.genre) + look("eras", direction.era) + look("pacing", direction.pacing),
        lights=_phrase_lights(lib, direction, dialect, subject, warnings),
        performance=look("emotions", direction.emotion) + _phrase_cast(lib, direction, dialect, names, warnings),
        speech=_phrase_speech(direction, names) if spec.prompt.dialogue and not is_image else [],
    )
    if direction.dialogue and not parts.speech:
        warnings.append(f"{spec.name} doesn't voice dialogue, so the lines were left out of the prompt")
    text = _render(dialect, prompt, parts, is_image)

    # Over the model's limit: drop the softest cues first, never the user's prompt or camera moves.
    limit = spec.prompt.max_chars
    for buckets in SHED_ORDER:
        if len(text) <= limit:
            break
        if any(getattr(parts, b) for b in buckets):
            for b in buckets:
                setattr(parts, b, [])
            warnings.append(f"dropped {buckets[0]} cues to fit the {limit}-character limit")
            text = _render(dialect, prompt, parts, is_image)
    if len(text) > limit:
        warnings.append(f"prompt is still {len(text)} characters; the model allows {limit}")
    return Compiled(prompt=text, warnings=warnings, native_params=native, dialect=dialect)
