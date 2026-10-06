"""Choosing a model's mode by what it needs, so recipes work with any model in the registry."""

from __future__ import annotations

from studio.registry.schema import Mode, ModelSpec


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
