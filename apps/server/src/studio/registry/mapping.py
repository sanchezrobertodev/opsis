"""Translate a normalized canonical request into a provider payload using a route's `map`.

Inputs are already staged: `inputs` maps media role -> provider-ready reference (URL, file id, or
base64), produced by the adapter's InputStager.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from studio.registry.schema import ModelSpec, ParamMapEntry, Route
from studio.registry.validate import NormalizedRequest


def _t_str(v: Any, _: dict) -> Any:
    return str(v)


def _t_int(v: Any, _: dict) -> Any:
    return int(v)


def _t_float(v: Any, _: dict) -> Any:
    return float(v)


def _t_enum_map(v: Any, args: dict) -> Any:
    mapping = args.get("values", {})
    return mapping.get(str(v), mapping.get(v, v))


def _t_seconds_to_frames(v: Any, args: dict) -> Any:
    return round(float(v) * float(args.get("fps", 24))) + int(args.get("plus", 0))


def _t_bool_to_str(v: Any, _: dict) -> Any:
    return "true" if v else "false"


def _t_suffix(v: Any, args: dict) -> Any:
    return f"{v}{args.get('suffix', '')}"


def _t_scale(v: Any, args: dict) -> Any:
    """Unit changes, e.g. seconds to milliseconds: {factor: 1000}."""
    return round(float(v) * float(args.get("factor", 1)))


def _t_shot_list(v: Any, args: dict) -> Any:
    """Multi-shot as the provider's own list: [{prompt, duration}], durations as `args.duration` (str or int)."""
    as_type = {"str": lambda d: str(round(d)), "int": lambda d: round(d)}.get(args.get("duration", ""), lambda d: d)
    return [{"prompt": s["prompt"], "duration": as_type(s["duration"])} for s in v]


def _t_timed_prompt(v: Any, args: dict) -> Any:
    """Multi-shot as one prompt for models that cut on cue: "Shot 1 (0-4 s): ...", then a cut to the next."""
    lines, start = [], 0.0
    for i, s in enumerate(v, 1):
        end = start + float(s["duration"])
        lines.append(f"Shot {i} ({start:g}-{end:g} s): {s['prompt']}")
        start = end
    return "\n".join([args.get("intro", "A multi-shot sequence with hard cuts between shots."), *lines])


TRANSFORMS: dict[str, Callable[[Any, dict], Any]] = {
    "shot_list": _t_shot_list,
    "timed_prompt": _t_timed_prompt,
    "str": _t_str,
    "int": _t_int,
    "float": _t_float,
    "enum_map": _t_enum_map,
    "seconds_to_frames": _t_seconds_to_frames,
    "bool_to_str": _t_bool_to_str,
    "suffix": _t_suffix,
    "scale": _t_scale,
}


def _set_path(payload: dict[str, Any], dotted: str, value: Any) -> None:
    """Support nested provider fields such as `input.prompt`."""
    parts = dotted.split(".")
    node = payload
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _load_hook(path: str) -> Callable[..., dict[str, Any]]:
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def build_payload(
    spec: ModelSpec, route: Route, req: NormalizedRequest, inputs: dict[str, Any]
) -> tuple[str, dict[str, Any], list[str]]:
    """Return (endpoint, payload, warnings)."""
    route_mode = route.modes.get(req.mode)
    if route_mode is None:
        raise ValueError(f"route {route.id} does not support mode {req.mode}")
    warnings: list[str] = []
    payload: dict[str, Any] = {}
    m = route.map

    if req.prompt and m.prompt:
        _set_path(payload, m.prompt, req.prompt)
    if req.negative_prompt and m.negative_prompt and "negative_prompt" not in route_mode.drop:
        _set_path(payload, m.negative_prompt, req.negative_prompt)

    for name, value in req.params.items():
        if name in route_mode.drop:
            continue
        entry = m.params.get(name)
        if name in route.unsupported_params:
            warnings.append(f"{route.id} ignores {name}")
            continue
        if entry is None:
            if m.passthrough:
                _set_path(payload, name, value)
            continue
        if isinstance(entry, str):
            _set_path(payload, entry, value)
            continue
        assert isinstance(entry, ParamMapEntry)
        if entry.to is None:
            continue
        if entry.transform:
            value = TRANSFORMS[entry.transform](value, entry.args)
        _set_path(payload, entry.to, value)

    for role, ref in inputs.items():
        field_name = route_mode.media.get(role) or m.media.get(role, role)
        slot = spec.mode(req.mode).slot(role) if spec.mode(req.mode) else None
        if field_name.endswith("[]"):  # the provider wants a list even for one file (e.g. `video_urls[]`)
            field_name = field_name[:-2]
            ref = ref if isinstance(ref, list) else [ref]
        elif slot and slot.max > 1 and not isinstance(ref, list):
            ref = [ref]
        elif slot and slot.max == 1 and isinstance(ref, list):
            ref = ref[0]
        _set_path(payload, field_name, ref)

    for key, value in route_mode.fixed.items():
        _set_path(payload, key, value)

    if route.transform:
        payload = _load_hook(route.transform)(spec=spec, route=route, req=req, payload=payload)

    return endpoint_for(route_mode.endpoint, req.params), payload, warnings


def endpoint_for(template: str, params: dict[str, Any]) -> str:
    """Fill `{param}` placeholders, for providers that put a quality tier in the path
    (e.g. `fal-ai/kling-video/v3/{tier}/motion-control`). Map such params `to: null` so they stay out of the body."""
    if "{" not in template:
        return template
    try:
        return template.format_map({k: str(v) for k, v in params.items()})
    except KeyError as exc:
        raise ValueError(f"endpoint {template!r} needs the {exc.args[0]!r} parameter") from None
