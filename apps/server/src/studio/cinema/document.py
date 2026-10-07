"""The Cinema Studio project document: scenes of shots, the takes picked for them, and the cut.

Stored as JSONB on `projects.document` and validated on every save. Shot ids are short and stable, so takes
(generations tagged with `shot_key`) stay attached to their shot while shots are reordered or rewritten.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.director.compiler import ShotDirection


def new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(3)}"


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Shot(_Doc):
    id: str = Field(default_factory=lambda: new_id("s"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    action: str = Field("", max_length=2000, description="What happens, with @mentions of saved characters")
    duration: int | None = Field(None, ge=1, le=30, description="Seconds; the model's default when empty")
    direction: ShotDirection = Field(default_factory=ShotDirection)
    dialogue: str = Field("", max_length=1000)
    continuity: Literal["cut", "continue"] = Field(
        "cut", description="continue: start from the last frame of the previous shot's take")
    hero_asset_id: str | None = Field(None, description="The picked opening frame")
    take_asset_id: str | None = Field(None, description="The picked take")
    trim_in: float = Field(0.0, ge=0)
    trim_out: float | None = Field(None, gt=0, description="Where the cut leaves this take; its end when empty")
    transition: Literal["cut", "fade"] = Field("cut", description="How the cut enters this shot")


class Scene(_Doc):
    id: str = Field(default_factory=lambda: new_id("c"), pattern=r"^[a-z][a-z0-9]{2,15}$")
    heading: str = Field("", max_length=200, description="e.g. EXT. HARBOUR - DAWN")
    summary: str = Field("", max_length=1000)
    shots: list[Shot] = Field(default_factory=list, max_length=60)


class Settings(_Doc):
    model_id: str = Field("seedance-2.5", description="The video model shots are filmed on")
    image_model_id: str = Field("nano-banana-2", description="Renders hero frames")
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    direction: ShotDirection = Field(default_factory=ShotDirection,
                                     description="Look applied to every shot unless the shot sets its own")
    takes_per_shot: int = Field(2, ge=1, le=4)
    hero_frames: int = Field(4, ge=1, le=4)


class CinemaDocument(_Doc):
    logline: str = Field("", max_length=600)
    script: str = Field("", max_length=40000)
    cast: list[str] = Field(default_factory=list, max_length=12, description="@mention slugs of saved Elements")
    settings: Settings = Field(default_factory=Settings)
    scenes: list[Scene] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _unique_ids(self) -> CinemaDocument:
        ids = [s.id for s in self.scenes] + [shot.id for s in self.scenes for shot in s.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("scene and shot ids must be unique")
        return self

    def shots(self) -> list[tuple[Scene, Shot]]:
        return [(scene, shot) for scene in self.scenes for shot in scene.shots]

    def shot(self, shot_id: str) -> Shot | None:
        return next((shot for _, shot in self.shots() if shot.id == shot_id), None)


def merged_direction(settings: Settings, shot: Shot) -> dict[str, Any]:
    """The project's look with the shot's own choices on top (a field the shot sets wins)."""
    base = settings.direction.model_dump(exclude_none=True)
    own = shot.direction.model_dump(exclude_none=True)
    if not own.get("camera_moves"):
        own.pop("camera_moves", None)
    return {**base, **own}
