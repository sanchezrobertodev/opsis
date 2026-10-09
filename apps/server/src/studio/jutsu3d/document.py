"""3D blocking scenes (`projects.kind = "jutsu3d"`): what the editor saves.

A scene is a list of objects (primitives, posable mannequins, imported or generated GLB models, Gaussian-splat
worlds, lights and cameras)
with transforms, plus a timeline of keyframes (camera position, target and focal length; object transforms) that
the browser renders into passes: beauty, depth and normals. Positions are metres, rotations are XYZ Euler radians,
y is up. Like Cinema films, scenes are versioned with revisions; runs never write them.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = r"^[a-z][a-z0-9]{2,15}$"
HEX = r"^#[0-9a-fA-F]{6}$"
Vec3 = tuple[float, float, float]

ObjectKind = Literal["box", "sphere", "cylinder", "cone", "plane", "mannequin", "model", "world", "sun",
                     "point_light", "spot_light", "camera"]
LIGHTS = ("sun", "point_light", "spot_light")
JOINTS = ("spine", "neck", "head", "shoulder_l", "elbow_l", "shoulder_r", "elbow_r", "hip_l", "knee_l", "hip_r",
          "knee_r")


def new_id(prefix: str = "o") -> str:
    return f"{prefix}{secrets.token_hex(3)}"


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transform(_Doc):
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec3 = (0.0, 0.0, 0.0)
    scale: Vec3 = (1.0, 1.0, 1.0)


class Animation(_Doc):
    """A rigged model playing a clip: the clip by name, from the model's own GLB or another on the same skeleton
    (a rigging job's walking or running GLB), placed on the timeline."""

    clip: str = Field(min_length=1, max_length=200, description="The clip's name in the GLB")
    source_asset_id: str | None = Field(None, description="The GLB holding the clip; the model's own when empty")
    start: float = Field(0.0, ge=0, le=60, description="Seconds into the timeline when the clip starts")
    offset: float = Field(0.0, ge=0, le=600, description="Seconds into the clip it starts from")
    speed: float = Field(1.0, gt=0, le=4)
    loop: bool = True


class SceneObject(_Doc):
    id: str = Field(default_factory=new_id, pattern=ID)
    kind: ObjectKind
    name: str = Field("", max_length=80)
    transform: Transform = Field(default_factory=Transform)
    color: str = Field("#b8b8b8", pattern=HEX)
    visible: bool = True
    asset_id: str | None = Field(None, description="model: the GLB asset; world: the splat asset")
    collider_asset_id: str | None = Field(None, description="world: its collision mesh (GLB), drawn in the depth pass")
    pose: dict[str, Vec3] = Field(default_factory=dict, description="mannequin: joint rotations by joint name")
    intensity: float = Field(1.0, ge=0, le=100, description="lights")
    target: Vec3 = Field((0.0, 1.0, 0.0), description="camera and spot light: the point they look at")
    focal_length: float = Field(35.0, ge=8, le=400, description="camera: millimetres on a full-frame sensor")
    animation: Animation | None = Field(None, description="model: a clip it plays on the timeline")

    @model_validator(mode="after")
    def _fits_kind(self) -> SceneObject:
        if self.kind == "model" and not self.asset_id:
            raise ValueError("a model object needs its GLB asset")
        if self.kind == "world" and not self.asset_id:
            raise ValueError("a world object needs its splat asset")
        if self.animation and self.kind != "model":
            raise ValueError("only a model object plays an animation clip")
        if unknown := set(self.pose) - set(JOINTS):
            raise ValueError(f"unknown mannequin joints {sorted(unknown)}")
        return self


class Keyframe(_Doc):
    """One moment on an object's track; fields left out hold their previous value."""

    t: float = Field(ge=0, le=60, description="seconds from the start")
    position: Vec3 | None = None
    rotation: Vec3 | None = None
    scale: Vec3 | None = None
    target: Vec3 | None = None
    focal_length: float | None = Field(None, ge=8, le=400)


class Track(_Doc):
    object_id: str = Field(pattern=ID)
    keys: list[Keyframe] = Field(default_factory=list, max_length=240)

    @model_validator(mode="after")
    def _in_order(self) -> Track:
        self.keys = sorted(self.keys, key=lambda k: k.t)
        return self


class Timeline(_Doc):
    duration: float = Field(5.0, ge=1, le=60)
    fps: int = Field(24, ge=8, le=60)
    tracks: list[Track] = Field(default_factory=list, max_length=200)


class SceneSettings(_Doc):
    background: str = Field("#1c1d20", pattern=HEX)
    aspect_ratio: Literal["16:9", "9:16", "1:1", "21:9", "4:3"] = "16:9"
    ambient: float = Field(0.4, ge=0, le=5)


class Jutsu3dDocument(_Doc):
    objects: list[SceneObject] = Field(default_factory=list, max_length=200)
    active_camera_id: str | None = Field(None, description="The camera renders look through")
    timeline: Timeline = Field(default_factory=Timeline)
    settings: SceneSettings = Field(default_factory=SceneSettings)
    cover_asset_id: str | None = Field(None, description="A still of the scene, for the list")

    @model_validator(mode="after")
    def _consistent(self) -> Jutsu3dDocument:
        ids = [o.id for o in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("object ids must be unique")
        if self.active_camera_id and not any(o.id == self.active_camera_id and o.kind == "camera"
                                             for o in self.objects):
            raise ValueError("the active camera must be a camera in the scene")
        known = set(ids)
        if missing := {t.object_id for t in self.timeline.tracks} - known:
            raise ValueError(f"tracks for objects not in the scene: {sorted(missing)}")
        return self

    def asset_ids(self) -> set[str]:
        own = {a for o in self.objects
               for a in (o.asset_id, o.collider_asset_id, o.animation.source_asset_id if o.animation else None) if a}
        return own | ({self.cover_asset_id} if self.cover_asset_id else set())

    def without_assets(self, gone: set[str]) -> tuple[Jutsu3dDocument, int]:
        """Objects whose model or world was deleted are left out, and a gone collider, clip source or cover is cleared.
        Returns how many went."""
        keep = [o for o in self.objects if not (o.asset_id and o.asset_id in gone)]
        dropped = len(self.objects) - len(keep)
        colliders = sum(1 for o in keep if o.collider_asset_id in gone)
        keep = [o.model_copy(update={"collider_asset_id": None}) if o.collider_asset_id in gone else o for o in keep]
        # a clip whose GLB went: the model stands still again
        clips = sum(1 for o in keep if o.animation and o.animation.source_asset_id in gone)
        keep = [o.model_copy(update={"animation": None}) if o.animation and o.animation.source_asset_id in gone else o
                for o in keep]
        ids = {o.id for o in keep}
        doc = self.model_copy(update={
            "objects": keep,
            "timeline": self.timeline.model_copy(update={
                "tracks": [t for t in self.timeline.tracks if t.object_id in ids]}),
            "active_camera_id": self.active_camera_id if self.active_camera_id in ids else None,
            "cover_asset_id": None if self.cover_asset_id in gone else self.cover_asset_id,
        })
        return doc, dropped + colliders + clips + (1 if self.cover_asset_id in gone else 0)


def starter_scene() -> dict[str, Any]:
    """A new scene: a camera looking at a mannequin standing on the floor, lit by the sun."""
    cam, man, sun = new_id("c"), new_id("m"), new_id("l")
    return Jutsu3dDocument(
        objects=[
            SceneObject(id=cam, kind="camera", name="Camera", transform=Transform(position=(0.0, 1.6, 5.0)),
                        target=(0.0, 1.1, 0.0)),
            SceneObject(id=man, kind="mannequin", name="Figure", color="#d9cbb8"),
            SceneObject(id=sun, kind="sun", name="Sun", transform=Transform(position=(4.0, 6.0, 3.0)), intensity=2.5,
                        color="#fff4e0"),
        ],
        active_camera_id=cam,
    ).model_dump(mode="json")
