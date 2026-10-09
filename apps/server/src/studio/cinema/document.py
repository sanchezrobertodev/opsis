"""The Cinema Studio project document: scenes of shots, the takes picked for them, and the cut (order, title
cards and the dialogue, music and SFX tracks).

Stored as JSONB on `projects.document` and validated on every save. Shot ids are short and stable, so takes
(generations tagged with `shot_key`) stay attached to their shot while shots are reordered or rewritten.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.director.compiler import MAX_LINES, ShotDirection


def new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(3)}"


MAX_CAST = 12


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShotLine(_Doc):
    """A line as the editor holds it: it may still be blank while being written (filming skips blank lines)."""

    who: str = Field("", max_length=60, description="An @mention of a cast member, or a name")
    text: str = Field("", max_length=500)
    voice_id: str | None = Field(None, description="Overrides the speaker's own voice")


class Shot(_Doc):
    id: str = Field(default_factory=lambda: new_id("s"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    action: str = Field("", max_length=2000, description="What happens, with @mentions of saved characters")
    duration: int | None = Field(None, ge=1, le=30, description="Seconds; the model's default when empty")
    direction: ShotDirection = Field(default_factory=ShotDirection)
    lines: list[ShotLine] = Field(default_factory=list, max_length=MAX_LINES,
                                      description="What's said in the shot, in order: who (an @mention or a name) and the "
                                                  "words; voice_id overrides the speaker's voice")
    continuity: Literal["cut", "continue"] = Field(
        "cut", description="continue: start from the last frame of the previous shot's take")
    hero_asset_id: str | None = Field(None, description="The picked opening frame")
    end_asset_id: str | None = Field(None, description="The picked closing frame: the take animates from its start to "
                                                       "this (models with a start-and-end-frame mode)")
    take_asset_id: str | None = Field(None, description="The picked take")
    trim_in: float = Field(0.0, ge=0)
    trim_out: float | None = Field(None, gt=0, description="Where the cut leaves this take; its end when empty")
    transition: Literal["cut", "fade"] = Field("cut", description="How the cut enters this shot")

    @model_validator(mode="before")
    @classmethod
    def _dialogue_as_lines(cls, data: Any) -> Any:
        """Dialogue used to be free text ("Name: line" per row); it becomes lines."""
        if isinstance(data, dict) and "dialogue" in data:
            data = dict(data)
            text = str(data.pop("dialogue") or "")
            if text.strip() and not data.get("lines"):
                data["lines"] = [_line(row) for row in text.splitlines() if row.strip()][:MAX_LINES]
        return data


def _line(row: str) -> dict[str, str]:
    who, sep, words = row.partition(":")
    if sep and words.strip() and len(who.strip()) <= 60:
        return {"who": who.strip(), "text": words.strip()[:500]}
    return {"who": "Narrator", "text": row.strip()[:500]}


class Scene(_Doc):
    id: str = Field(default_factory=lambda: new_id("c"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    heading: str = Field("", max_length=200, description="e.g. EXT. HARBOUR - DAWN")
    summary: str = Field("", max_length=1000)
    shots: list[Shot] = Field(default_factory=list, max_length=60)


class Settings(_Doc):
    model_id: str = Field("cinema-studio", description="The video model shots are filmed on")
    model_params: dict[str, Any] = Field(default_factory=dict,
                                         description="More params for that model, such as Cinema Studio's engine")
    image_model_id: str = Field("nano-banana-2", description="Renders hero frames")
    lipsync_model_id: str = Field("sync-lipsync-3", description="Lip-syncs dialogue on models that don't voice it")
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    direction: ShotDirection = Field(default_factory=ShotDirection,
                                     description="Look applied to every shot unless the shot sets its own")
    takes_per_shot: int = Field(2, ge=1, le=4)
    hero_frames: int = Field(4, ge=1, le=9, description="Frames per grid: 4 is 2 x 2, 9 is 3 x 3")


HEX = r"^#[0-9a-fA-F]{6}$"


class TitleCard(_Doc):
    text: str = Field(min_length=1, max_length=200)
    subtext: str = Field("", max_length=200)
    duration: float = Field(3.0, ge=0.5, le=20)
    background: str = Field("#0d0e10", pattern=HEX)
    color: str = Field("#f2efe8", pattern=HEX)
    transition: Literal["cut", "fade"] = Field("fade", description="How the cut enters the card")


class CutItem(_Doc):
    """One place on the picture track: a shot (its picked take, trimmed as the shot says) or a title card."""

    id: str = Field(default_factory=lambda: new_id("i"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    shot_id: str | None = None
    card: TitleCard | None = None
    volume: float = Field(1.0, ge=0, le=2, description="The take's own sound; 0 when its dialogue is on a track")

    @model_validator(mode="after")
    def _one(self) -> CutItem:
        if (self.shot_id is None) == (self.card is None):
            raise ValueError("a cut item is either a shot or a title card")
        return self


class AudioClip(_Doc):
    """A sound on one of the cut's tracks, placed `start` seconds into the film."""

    id: str = Field(default_factory=lambda: new_id("a"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    track: Literal["dialogue", "music", "sfx"]
    asset_id: str
    label: str = Field("", max_length=120)
    start: float = Field(0.0, ge=0, le=3600)
    trim_in: float = Field(0.0, ge=0)
    length: float | None = Field(None, gt=0, description="Seconds used from trim_in; to the clip's end when empty")
    volume: float = Field(1.0, ge=0, le=2)
    fade_in: float = Field(0.0, ge=0, le=10)
    fade_out: float = Field(0.0, ge=0, le=10)
    shot_id: str | None = Field(None, description="Dialogue that belongs to a shot moves with it")


class Cut(_Doc):
    """The edit. With no items, every picked take plays in story order; shots added later join at the end."""

    items: list[CutItem] = Field(default_factory=list, max_length=200)
    audio: list[AudioClip] = Field(default_factory=list, max_length=100)
    duck_music: bool = Field(True, description="Lower the music while dialogue plays")
    fade_seconds: float = Field(0.5, ge=0.1, le=2.0)


class CinemaDocument(_Doc):
    logline: str = Field("", max_length=600)
    script: str = Field("", max_length=40000)
    cast: list[str] = Field(default_factory=list, max_length=MAX_CAST, description="@mention slugs of saved Elements")
    settings: Settings = Field(default_factory=Settings)
    scenes: list[Scene] = Field(default_factory=list, max_length=40)
    cut: Cut = Field(default_factory=Cut)

    @model_validator(mode="after")
    def _unique_ids(self) -> CinemaDocument:
        ids = [s.id for s in self.scenes] + [shot.id for s in self.scenes for shot in s.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("scene and shot ids must be unique")
        # a shot deleted from the storyboard leaves the cut too, with any dialogue tied to it
        known = set(ids)
        seen: set[str] = set()
        items = []
        for item in self.cut.items:
            if item.shot_id is not None and (item.shot_id not in known or item.shot_id in seen):
                continue
            seen.add(item.shot_id or item.id)
            items.append(item)
        audio = [a for a in self.cut.audio if a.shot_id is None or a.shot_id in known]
        if len(items) != len(self.cut.items) or len(audio) != len(self.cut.audio):
            self.cut = self.cut.model_copy(update={"items": items, "audio": audio})
        return self

    def shots(self) -> list[tuple[Scene, Shot]]:
        return [(scene, shot) for scene in self.scenes for shot in scene.shots]

    def shot(self, shot_id: str) -> Shot | None:
        return next((shot for _, shot in self.shots() if shot.id == shot_id), None)


def merged_direction(settings: Settings, shot: Shot) -> dict[str, Any]:
    """The project's look with the shot's own choices on top (a field the shot sets wins)."""
    def chosen(d: ShotDirection) -> dict[str, Any]:  # lists left empty (moves, lights, emotions) choose nothing
        return {k: v for k, v in d.model_dump(exclude_none=True).items() if v != []}

    return {"camera_moves": [], **chosen(settings.direction), **chosen(shot.direction)}
