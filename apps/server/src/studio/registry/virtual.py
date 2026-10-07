"""Virtual models (`kind: virtual`): a studio layer such as Cinema Studio over base models.

A request for a virtual model names an engine; `resolve` turns it into an ordinary request for that engine's base
model and mode before anything else happens. Routing, pricing, the spend guard, ingest and retries then see only
the base model, so a virtual model can never cost or behave differently from running its engine directly.

Params the virtual model shares with its engines are carried over when the engine takes them, fitted when it
takes something close (a duration rounds to one it allows) and dropped with a warning otherwise. Director params
(era, lens, palette...) become shot direction; a choice made in the direction itself wins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from studio.registry.schema import EnumParam, FloatParam, IntParam, ModelSpec
from studio.registry.validate import RequestError

if TYPE_CHECKING:
    from studio.registry.loader import Registry


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


@dataclass
class Resolved:
    spec: ModelSpec  # the model that actually runs
    mode: str
    params: dict[str, Any]
    direction: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)
    via: dict[str, Any] | None = None  # {model_id, name, mode, engine} when a virtual model was asked for


def engine_id(spec: ModelSpec, params: dict[str, Any]) -> str | None:
    """The engine a request picks: its engine param, else the param's default, else the first engine."""
    v = spec.virtual
    if v is None:
        return None
    p = spec.param(v.engine_param)
    chosen = params.get(v.engine_param) or (p.default if isinstance(p, EnumParam) else None)
    return str(chosen) if chosen else (v.engines[0].id if v.engines else None)


def resolve(reg: Registry, spec: ModelSpec, mode_id: str, params: dict[str, Any],
            direction: dict[str, Any] | None) -> Resolved:
    """The base model, mode, params and direction a request runs as. Non-virtual models pass through unchanged."""
    if spec.kind != "virtual" or spec.virtual is None:
        return Resolved(spec, mode_id, dict(params), direction)
    v = spec.virtual
    mode = spec.mode(mode_id)
    if mode is None:
        raise RequestError([f"{spec.name} has no mode {mode_id!r}"])
    engine = v.engine(engine_id(spec, params))
    if engine is None:
        raise RequestError([f"{spec.name} has no engine {params.get(v.engine_param)!r}; "
                            f"pick one of {', '.join(e.id for e in v.engines)}"])
    target = engine.target(mode_id)
    if target is None:
        can = [e.label for e in v.engines if e.target(mode_id)]
        raise RequestError([f"{engine.label} can't do {mode.label.lower()} in {spec.name}"
                            + (f"; {' or '.join(can)} can" if can else "")])
    base = reg.model(target[0])
    if base is None or base.mode(target[1]) is None:
        raise RequestError([f"{spec.name}: engine {engine.id} points at {'/'.join(target)}, which doesn't exist"])

    rest = {k: val for k, val in params.items() if k != v.engine_param}
    out_direction = dict(direction or {})
    for name in v.director:
        value = rest.pop(name, None)
        if value not in (None, "") and not out_direction.get(name):
            out_direction[name] = value

    warnings: list[str] = []
    out: dict[str, Any] = {}
    for name, value in rest.items():
        theirs = next((p for p in base.params_for(target[1]) if p.name == name), None)
        ours = spec.param(name)
        label = (ours.label if ours and ours.label else name.replace("_", " ")).lower()
        if theirs is None:
            if ours is not None and value != getattr(ours, "default", None):
                warnings.append(f"{base.name} doesn't take {label}; left out")
            continue
        if name == "duration" and value is not None:
            fitted = fit_duration(base, target[1], float(value))
            if fitted is not None and float(fitted) != float(value):
                warnings.append(f"{base.name} makes {fitted} s clips, not {value} s")
            out[name] = fitted if fitted is not None else value
        elif isinstance(theirs, EnumParam) and value not in theirs.options:
            warnings.append(f"{base.name} has no {value} {label}; using its default")
        elif isinstance(theirs, (IntParam, FloatParam)) and isinstance(value, (int, float)) \
                and not theirs.min <= value <= theirs.max:
            out[name] = min(theirs.max, max(theirs.min, value))
            warnings.append(f"{base.name} takes {label} from {theirs.min} to {theirs.max}; using {out[name]}")
        else:
            out[name] = value
    return Resolved(base, target[1], out, out_direction or None, warnings,
                    via={"model_id": spec.id, "name": spec.name, "mode": mode_id, "engine": engine.id,
                         "engine_label": engine.label})
