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
    PathsParam,
    PointsParam,
    SectionsParam,
    SeedParam,
    ShotsParam,
    TextParam,
)


@dataclass
class MediaRef:
    role: str
    asset_id: str
    kind: str | None = None  # filled by the API from the asset row
    duration_ms: int | None = None  # for video/audio inputs; some routes bill by input length
    fps: float | None = None  # for video inputs; some routes bill per input frame
    width: int | None = None  # pixel size, for routes that send coordinates (SAM points)
    height: int | None = None
    size_bytes: int | None = None  # the file's size, checked against a slot's max_mb


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
                       "fps": m.fps, "width": m.width, "height": m.height} for m in self.media],
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
    if isinstance(param, PathsParam):
        return _coerce_paths(param, value)
    if isinstance(param, PointsParam):
        return _coerce_points(param, value)
    if isinstance(param, ShotsParam):
        return _coerce_shots(param, value)
    if isinstance(param, SectionsParam):
        return _coerce_sections(param, value)
    if isinstance(param, EnumParam):
        # enum options may be ints (e.g. Veo durations 4/6/8); match by string form
        for opt in param.options:
            if str(opt) == str(value):
                return opt
        if param.open and str(value).strip():
            return str(value)
        raise ValueError(f"must be one of {param.options}")
    return str(value)


def _coerce_paths(param: PathsParam, value: Any) -> list[dict[str, list[list[float]]]]:
    if not isinstance(value, list):
        raise ValueError("must be a list of paths")
    if len(value) > param.max_paths:
        raise ValueError(f"at most {param.max_paths} paths")
    paths = []
    for path in value:
        points = path.get("points") if isinstance(path, dict) else None
        if not isinstance(points, list) or not 1 <= len(points) <= param.max_points:
            raise ValueError(f"each path needs 1 to {param.max_points} points")
        clean = []
        for pt in points:
            if not (isinstance(pt, list | tuple) and len(pt) == 2):
                raise ValueError("points are [x, y] pairs")
            x, y = float(pt[0]), float(pt[1])
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("points must lie inside the frame (0 to 1)")
            clean.append([round(x, 4), round(y, 4)])
        paths.append({"points": clean})
    return paths


def _coerce_points(param: PointsParam, value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        raise ValueError("must be a list of [x, y, label] points")
    if len(value) > param.max_points:
        raise ValueError(f"at most {param.max_points} points")
    clean = []
    for pt in value:
        if not (isinstance(pt, list | tuple) and len(pt) == 3 and pt[2] in (0, 1)):
            raise ValueError("points are [x, y, label] with label 1 (this) or 0 (not this)")
        x, y = float(pt[0]), float(pt[1])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("points must lie inside the image (0 to 1)")
        clean.append([round(x, 4), round(y, 4), int(pt[2])])
    return clean


def _coerce_shots(param: ShotsParam, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("must be a list of shots")
    if len(value) > param.max_shots:
        raise ValueError(f"at most {param.max_shots} shots")
    shots = []
    for i, shot in enumerate(value, 1):
        if not isinstance(shot, dict) or not str(shot.get("prompt") or "").strip():
            raise ValueError(f"shot {i} needs a description")
        try:
            seconds = float(shot.get("duration") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"shot {i}: duration must be a number of seconds") from None
        if not param.min_shot_s <= seconds <= param.max_shot_s:
            raise ValueError(f"shot {i} must last {param.min_shot_s:g} to {param.max_shot_s:g} s")
        clean: dict[str, Any] = {"prompt": str(shot["prompt"]).strip(), "duration": round(seconds, 2)}
        if isinstance(shot.get("direction"), dict) and shot["direction"]:
            clean["direction"] = shot["direction"]
        shots.append(clean)
    total = sum(s["duration"] for s in shots)
    if total > param.max_total_s + 1e-6:
        raise ValueError(f"the shots add up to {total:g} s; at most {param.max_total_s:g} s in one take")
    return shots


def _coerce_sections(param: SectionsParam, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("must be a list of song sections")
    if len(value) > param.max_sections:
        raise ValueError(f"at most {param.max_sections} sections")
    sections = []
    for i, sec in enumerate(value, 1):
        if not isinstance(sec, dict):
            raise ValueError(f"section {i} must be {{name, lines, duration}}")
        name = str(sec.get("name") or f"Section {i}").strip()[:100]
        lines = [str(x).strip() for x in sec.get("lines") or [] if str(x).strip()]
        if len(lines) > param.max_lines:
            raise ValueError(f"section {i} has {len(lines)} lines; at most {param.max_lines}")
        if any(len(x) > param.max_line_chars for x in lines):
            raise ValueError(f"section {i}: lines can be at most {param.max_line_chars} characters")
        try:
            seconds = float(sec.get("duration") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"section {i}: duration must be a number of seconds") from None
        if not param.min_section_s <= seconds <= param.max_section_s:
            raise ValueError(f"section {i} must last {param.min_section_s:g} to {param.max_section_s:g} s")
        sections.append({"name": name, "lines": lines, "duration": round(seconds, 2)})
    return sections


def _paths_needed(n: int) -> str:
    return "Draw at least one motion path on the start frame." if n == 1 else f"Draw at least {n} motion paths."


def _check(param: Param, value: Any) -> str | None:
    if isinstance(param, PathsParam) and len(value) < param.min_paths:
        return _paths_needed(param.min_paths)
    if isinstance(param, ShotsParam) and len(value) < param.min_shots:
        return f"Add at least {param.min_shots} shots."
    if isinstance(param, SectionsParam) and len(value) < param.min_sections:
        return f"Add at least {param.min_sections} song section{'s' if param.min_sections > 1 else ''}."
    if isinstance(param, PointsParam) and sum(1 for p in value if p[2] == 1) < param.min_points:
        return "Click on the object you want to select."
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
                if isinstance(param, PathsParam) and param.min_paths:
                    errors.append(_paths_needed(param.min_paths))
                if isinstance(param, PointsParam) and param.min_points:
                    errors.append("Click on the object you want to select.")
                if isinstance(param, ShotsParam) and param.min_shots:
                    errors.append(f"Add at least {param.min_shots} shots.")
                if isinstance(param, SectionsParam) and param.min_sections:
                    errors.append("Write the song's sections.")
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

    # a multi-shot take is as long as its shots together, and a song as its sections
    timed = next((p for p in allowed.values() if isinstance(p, ShotsParam | SectionsParam) and out.get(p.name)), None)
    if timed and isinstance(allowed.get("duration"), (IntParam, FloatParam)):
        total = sum(s["duration"] for s in out[timed.name])
        d = allowed["duration"]
        out["duration"] = round(total) if isinstance(d, IntParam) else round(total, 2)
        if not d.min <= out["duration"] <= d.max:
            what = "shots" if isinstance(timed, ShotsParam) else "sections"
            errors.append(f"the {what} add up to {total:g} s; {spec.name} makes {d.min:g} to {d.max:g} s")

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
            seconds = (ref.duration_ms or 0) / 1000
            if slot.max_duration_s and seconds > slot.max_duration_s + 0.05:
                errors.append(f"{slot.label or slot.role} can be at most {slot.max_duration_s:g} s; "
                              f"this one is {seconds:.1f} s")
            if slot.max_mb and ref.size_bytes and ref.size_bytes > slot.max_mb * 1024 * 1024:
                errors.append(f"{slot.label or slot.role} can be at most {slot.max_mb:g} MB; this one is "
                              f"{ref.size_bytes / (1024 * 1024):.0f} MB")
            short = min(ref.width, ref.height) if ref.width and ref.height else None
            if slot.max_pixels is not None or slot.min_aspect is not None or slot.max_aspect is not None:
                label = slot.label or slot.role
                if not ref.width or not ref.height or ref.width <= 0 or ref.height <= 0:
                    errors.append(f"{label} needs known image dimensions; upload the image again")
                else:
                    if slot.max_pixels is not None and ref.width * ref.height > slot.max_pixels:
                        errors.append(f"{label} can be at most {slot.max_pixels:,} pixels; this one is "
                                      f"{ref.width}x{ref.height} (resize it first)")
                    aspect = ref.width / ref.height
                    if ((slot.min_aspect is not None and aspect < slot.min_aspect)
                            or (slot.max_aspect is not None and aspect > slot.max_aspect)):
                        errors.append(f"{label} aspect ratio must be between {slot.min_aspect or 0:g} and "
                                      f"{slot.max_aspect if slot.max_aspect is not None else float('inf'):g} "
                                      f"(width / height); this one is {ref.width}x{ref.height} (crop it first)")
            if slot.min_side and short and short < slot.min_side:
                errors.append(f"{slot.label or slot.role} must be at least {slot.min_side} px on each side; this one "
                              f"is {ref.width}x{ref.height} (upscale it first)")
            if slot.min_fps and ref.fps and ref.fps < slot.min_fps - 0.01:
                errors.append(f"{slot.label or slot.role} must be at least {slot.min_fps:g} fps; this one is "
                              f"{ref.fps:g} fps (raise its frame rate first)")

    for rule in spec.rules:  # inputs some settings can't take (OmniHuman at 1080p: 30 s of audio; Marble: 4 pictures)
        context = {**out, "mode": mode_id}
        if (rule.max_input_seconds or rule.max_inputs) and all(
                context.get(k) in (v if isinstance(v, list) else [v]) for k, v in rule.when.items()):
            for role, limit in rule.max_input_seconds.items():
                seconds = sum((r.duration_ms or 0) for r in by_role.get(role, [])) / 1000
                if seconds > limit + 0.05:
                    errors.append(rule.message)
            if any(len(by_role.get(role, [])) > limit for role, limit in rule.max_inputs.items()):
                errors.append(rule.message)

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
