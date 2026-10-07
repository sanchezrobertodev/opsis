"""Choosing a model's mode by what it needs, so recipes work with any model in the registry."""

from __future__ import annotations

import math

from studio.registry.schema import EnumParam, FloatParam, IntParam, Mode, ModelSpec


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


def fit_duration(spec: ModelSpec, mode_id: str, seconds: float) -> int | float | str | None:
    """The shortest duration the model allows that covers `seconds` (its longest if none does), in the param's
    own type. None when the model has no duration param in this mode."""
    p = next((p for p in spec.params_for(mode_id) if p.name == "duration"), None)
    if isinstance(p, EnumParam):
        numeric = []
        for o in p.options:
            try:
                numeric.append((float(o), o))
            except ValueError:
                continue
        if not numeric:
            return None
        numeric.sort()
        return next((o for v, o in numeric if v >= seconds), numeric[-1][1])
    if isinstance(p, IntParam):
        steps = math.ceil(max(0.0, seconds - p.min) / p.step)
        return min(p.max, p.min + steps * p.step)
    if isinstance(p, FloatParam):
        return round(min(p.max, max(p.min, seconds)), 2)
    return None
