"""Choosing a model's mode by what it needs, so recipes work with any model in the registry."""

from __future__ import annotations

from studio.registry.schema import Mode, ModelSpec
from studio.registry.virtual import fit_duration


def text_mode(spec: ModelSpec) -> Mode | None:
    """A mode that needs nothing but a prompt (text to video / image)."""
    return next((m for m in spec.modes if m.prompt != "none" and not any(s.required for s in m.medias)), None)


def start_frame_mode(spec: ModelSpec) -> Mode | None:
    """A mode driven by a start frame and a prompt, with no other required inputs."""
    for m in spec.modes:
        slot = m.slot("start_image")
        if slot and m.prompt != "none" and all(s.role == "start_image" or not s.required for s in m.medias):
            return m
    return None


def mode_for(spec: ModelSpec, roles: set[str]) -> Mode | None:
    """The mode to use given the media roles on hand: a start frame picks image-to-video."""
    if "start_image" in roles and (m := start_frame_mode(spec)):
        return m
    return text_mode(spec)


def with_param(spec: ModelSpec, mode_id: str, params: dict, name: str, value: object) -> dict:
    """Set a param only if the model takes it in this mode."""
    if value is None or not any(p.name == name for p in spec.params_for(mode_id)):
        return params
    return {**params, name: value}


def mode_with_op(spec: ModelSpec, op: str) -> Mode | None:
    """The first mode that does `op` (e.g. tts, music, t2i)."""
    return next((m for m in spec.modes if op in m.ops), None)


def multi_shot_mode(spec: ModelSpec, *, frame: bool) -> Mode | None:
    """A mode that renders several shots in one take, opening on a given first frame or not."""
    for m in spec.modes:
        slot = m.slot("start_image")
        others_optional = all(s.role == "start_image" or not s.required for s in m.medias)
        if "multi_shot" in m.ops and bool(slot and slot.required) == frame and others_optional:
            return m
    return None


def bridge_mode(spec: ModelSpec) -> Mode | None:
    """A mode that animates from a start frame to a chosen end frame (to lead into an existing clip)."""
    for m in spec.modes:
        others_optional = all(s.role in ("start_image", "end_image") or not s.required for s in m.medias)
        if m.slot("start_image") and m.slot("end_image") and m.prompt != "none" and others_optional:
            return m
    return None


def edit_mode(spec: ModelSpec) -> Mode | None:
    """A mode that changes a whole clip by instruction."""
    for m in spec.modes:
        slot = m.slot("input_video")
        if "instruction_edit" in m.ops and slot and slot.required and m.prompt != "none":
            return m
    return None


__all__ = ["bridge_mode", "edit_mode", "fit_duration", "mode_for", "mode_with_op", "multi_shot_mode", "start_frame_mode",
           "text_mode", "with_param"]
