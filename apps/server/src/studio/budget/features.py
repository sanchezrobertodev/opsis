"""Which studio feature a charge paid for, from what the studio already records: the recipe a run used, the project a
take belongs to, and otherwise the op tags of the model's mode (the same tags the studios pick models by)."""

from __future__ import annotations

from studio.registry.schema import ModelSpec

LABELS = {
    "create_video": "Create video", "create_image": "Create image", "edit": "Edit video", "motion": "Motion transfer",
    "swap": "Object swap", "lipsync": "Lipsync", "audio": "Voice, music and sound", "faceless": "Faceless studio",
    "cinema": "Cinema studio", "jutsu3d": "3D blocking", "worlds": "Worlds", "characters": "Characters",
    "templates": "Templates", "claude": "Claude", "other": "Other",
}

# recipes that belong to one studio; compare runs from several (image, video, edit, motion), so its charges are
# classified by the mode each generation ran
RECIPES = {"faceless": "faceless", "object_swap": "swap", "talking_shot": "lipsync", "jutsu3d": "jutsu3d",
           "sequence": "create_video", "keyframe": "create_video", "lora_train": "characters",
           "vlog": "templates"}
PROJECTS = {"cinema": "cinema", "jutsu3d": "jutsu3d"}
# first match wins, so a lipsync or tracking mode isn't counted as plain video
OPS = [({"lora_train"}, "characters"), ({"lipsync"}, "lipsync"), ({"motion_transfer"}, "motion"), ({"segment", "track"}, "swap"),
       ({"tts", "sfx", "music", "song"}, "audio"), ({"world", "panorama"}, "worlds"), ({"to_3d", "depth_to_video", "render_to_real", "rig"}, "jutsu3d"),
       ({"t2i", "i2i"}, "create_image"),
       ({"instruction_edit", "restyle", "relight", "extend", "reframe", "upscale", "interpolate", "bg_remove",
         "add_sound", "stabilize", "trim", "retime", "reverse", "loop", "object_replace"}, "edit"),
       ({"t2v", "i2v", "flf2v", "multi_ref", "multi_shot", "avatar"}, "create_video")]


def claude_name(model: str) -> str:
    """claude-opus-5-5 -> Claude Opus 5.5"""
    parts = model.split("-")
    words = [p for p in parts if not p.isdigit()]
    numbers = [p for p in parts if p.isdigit()]
    return " ".join(w.capitalize() for w in words) + (f" {'.'.join(numbers)}" if numbers else "")


def feature_of(*, recipe: str | None, project_kind: str | None, spec: ModelSpec | None, mode_id: str | None) -> str:
    if recipe:
        if recipe.startswith("cinema_"):
            return "cinema"
        if recipe in RECIPES:
            return RECIPES[recipe]
    if project_kind in PROJECTS:
        return PROJECTS[project_kind]
    mode = spec.mode(mode_id) if spec and mode_id else None
    ops = set(mode.ops) if mode else set()
    return next((feature for tags, feature in OPS if ops & tags), "other")
