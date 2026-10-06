"""Validate and normalize a canonical generation request against a model spec.

The API calls this before creating a generation; the worker trusts its output. Normalization applies
defaults, coerces types and checks media slot counts, so every downstream consumer (pricing, routing,
payload mapping) sees the same complete parameter set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from studio.registry.schema import (
    BoolParam,
    EnumParam,
    FloatParam,
    IntParam,
    ModelSpec,
    Param,
    SeedParam,
    TextParam,
)


@dataclass
class MediaRef:
    role: str
    asset_id: str
    kind: str | None = None  # filled by the API from the asset row
    duration_ms: int | None = None  # for video/audio inputs; some routes bill by input length
    fps: float | None = None  # for video inputs; some routes bill per input frame


@dataclass
class NormalizedRequest:
    model_id: str
    mode: str
    prompt: str
    negative_prompt: str | None
    params: dict[str, Any]
    media: list[MediaRef]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "mode": self.mode,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "media": [{"role": m.role, "asset_id": m.asset_id, "kind": m.kind, "duration_ms": m.duration_ms,
                       "fps": m.fps} for m in self.media],
            "warnings": self.warnings,
        }


class RequestError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _coerce(param: Param, value: Any) -> Any:
    if isinstance(param, BoolParam):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(param, (IntParam, SeedParam)):
        return int(value)
    if isinstance(param, FloatParam):
        return float(value)
    if isinstance(param, EnumParam):
        # enum options may be ints (e.g. Veo durations 4/6/8); match by string form
        for opt in param.options:
            if str(opt) == str(value):
                return opt
        raise ValueError(f"must be one of {param.options}")
    return str(value)


def _check(param: Param, value: Any) -> str | None:
    if isinstance(param, (IntParam, FloatParam)) and not (param.min <= value <= param.max):
        return f"{param.name} must be between {param.min} and {param.max}"
    if isinstance(param, TextParam) and param.max_chars and len(value) > param.max_chars:
        return f"{param.name} exceeds {param.max_chars} characters"
    return None


def normalize(
    spec: ModelSpec,
    mode_id: str,
    prompt: str,
    params: dict[str, Any] | None,
    media: list[MediaRef] | None,
    negative_prompt: str | None = None,
) -> NormalizedRequest:
    errors: list[str] = []
    warnings: list[str] = []
    mode = spec.mode(mode_id)
    if mode is None:
        raise RequestError([f"model {spec.id} has no mode {mode_id!r}"])
    if spec.status == "shutdown":
        raise RequestError([f"model {spec.id} is shut down"])

    prompt = (prompt or "").strip()
    if mode.prompt == "required" and not prompt:
        errors.append("prompt is required")
    if mode.prompt == "none" and prompt:
        warnings.append("this mode ignores the prompt")
        prompt = ""
    if len(prompt) > spec.prompt.max_chars:
        errors.append(f"prompt exceeds {spec.prompt.max_chars} characters")
    if negative_prompt and not spec.prompt.supports_negative:
        warnings.append("negative prompt is not supported by this model and was dropped")
        negative_prompt = None

    given = dict(params or {})
    allowed = {p.name: p for p in spec.params_for(mode_id)}
    out: dict[str, Any] = {}
    for name in list(given):
        if name not in allowed:
            warnings.append(f"unknown parameter {name!r} ignored")
            given.pop(name)
    for name, param in allowed.items():
        raw = given.get(name)
        if raw is None or raw == "":
            if isinstance(param, SeedParam):
                out[name] = random.randint(0, 2**31 - 1)
                continue
            if param.default is None:
                continue
            raw = param.default
        try:
            value = _coerce(param, raw)
        except (TypeError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        problem = _check(param, value)
        if problem:
            errors.append(problem)
        out[name] = value

    if "aspect_ratio" in out and spec.aspect_ratios and out["aspect_ratio"] not in spec.aspect_ratios:
        errors.append(f"aspect_ratio must be one of {spec.aspect_ratios}")

    for rule in spec.rules:
        context = {**out, "mode": mode_id}  # rules may depend on the mode as well as params
        applies = all(
            context.get(k) in (v if isinstance(v, list) else [v]) for k, v in rule.when.items()
        )
        if applies:
            for key, options in rule.require.items():
                if out.get(key) not in options:
                    errors.append(rule.message)

    refs = list(media or [])
    by_role: dict[str, list[MediaRef]] = {}
    for ref in refs:
        if mode.slot(ref.role) is None:
            errors.append(f"mode {mode_id!r} has no media slot {ref.role!r}")
            continue
        by_role.setdefault(ref.role, []).append(ref)
    for slot in mode.medias:
        got = by_role.get(slot.role, [])
        if slot.required and not got:
            errors.append(f"{slot.label or slot.role} is required")
        if len(got) > slot.max:
            errors.append(f"{slot.role} accepts at most {slot.max}")
        if len(got) < slot.min:
            errors.append(f"{slot.role} needs at least {slot.min}")
        for ref in got:
            if ref.kind and ref.kind not in slot.kinds:
                errors.append(f"{slot.role} must be {'/'.join(slot.kinds)}, got {ref.kind}")

    if errors:
        raise RequestError(errors)
    return NormalizedRequest(
        model_id=spec.id,
        mode=mode_id,
        prompt=prompt,
        negative_prompt=negative_prompt or None,
        params=out,
        media=[r for role_refs in by_role.values() for r in role_refs],
        warnings=warnings,
    )
