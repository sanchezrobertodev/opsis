"""Presets: one-click templates over a model (prompt template, params, direction, required inputs).

They live in registry/presets/*.yaml next to the models they use. Rendering fills the template from the
user's answers and returns an ordinary generation request, so presets go through the same planning,
spend guard and routing as anything typed into Create.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from studio.registry.schema import PresetSpec
from studio.registry.validate import MediaRef, RequestError, normalize

if TYPE_CHECKING:
    from studio.registry.loader import Registry

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class PresetError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def placeholders(template: str) -> list[str]:
    return list(dict.fromkeys(PLACEHOLDER.findall(template)))


def render_prompt(preset: PresetSpec, values: dict[str, str]) -> str:
    """Fill {key} blanks. Unknown keys are ignored; missing required ones are an error."""
    errors: list[str] = []
    filled: dict[str, str] = {}
    for f in preset.fields:
        value = " ".join(str(values.get(f.key) or "").split())[: f.max_chars] or f.default
        if not value and f.required:
            errors.append(f"{f.label} is required")
        filled[f.key] = value
    if errors:
        raise PresetError(errors)
    text = PLACEHOLDER.sub(lambda m: filled.get(m.group(1), ""), preset.prompt)
    # optional blanks left empty shouldn't leave stray punctuation or double spaces behind
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    return " ".join(text.split()).strip(" ,")


def render(preset: PresetSpec, values: dict[str, str], media: list[dict[str, str]]) -> dict[str, Any]:
    """The generation request this preset makes with these answers and inputs."""
    missing = [i.label or i.role.replace("_", " ") for i in preset.inputs if not any(m["role"] == i.role for m in media)]
    if missing:
        raise PresetError([f"add {', '.join(missing)}"])
    allowed = {i.role for i in preset.inputs}
    return {
        "model_id": preset.model_id,
        "mode": preset.mode,
        "prompt": render_prompt(preset, values),
        "params": dict(preset.params),
        "media": [m for m in media if m["role"] in allowed],
        "direction": preset.direction,
    }


def lint_presets(reg: Registry) -> list[str]:
    from studio.director.compiler import ShotDirection
    from studio.director.library import FIELD_SECTIONS, get_library

    errors: list[str] = []
    library = get_library()
    for p in reg.presets.values():
        where = f"preset {p.id}"
        spec = reg.model(p.model_id)
        if spec is None:
            errors.append(f"{where}: unknown model {p.model_id!r}")
            continue
        mode = spec.mode(p.mode)
        if mode is None:
            errors.append(f"{where}: {spec.id} has no mode {p.mode!r}")
            continue

        keys = [f.key for f in p.fields]
        used = placeholders(p.prompt)
        if len(keys) != len(set(keys)):
            errors.append(f"{where}: duplicate field keys")
        if unknown := set(used) - set(keys):
            errors.append(f"{where}: template uses undeclared fields {sorted(unknown)}")
        if unused := set(keys) - set(used):
            errors.append(f"{where}: fields never used in the template {sorted(unused)}")

        for i in p.inputs:
            if mode.slot(i.role) is None:
                errors.append(f"{where}: mode {p.mode} has no media slot {i.role!r}")
        required = {s.role for s in mode.medias if s.required}
        if lacking := required - {i.role for i in p.inputs}:
            errors.append(f"{where}: mode {p.mode} needs inputs for {sorted(lacking)}")

        refs = [MediaRef(role=i.role, asset_id=f"lint-{i.role}", kind=mode.slot(i.role).kinds[0])  # type: ignore[union-attr]
                for i in p.inputs if mode.slot(i.role)]
        sample = render_prompt(p, {f.key: f.placeholder or f.label for f in p.fields})
        try:
            norm = normalize(spec, p.mode, sample, p.params, refs)
            errors += [f"{where}: {w}" for w in norm.warnings if "unknown parameter" in w]
        except RequestError as exc:
            errors += [f"{where}: {e}" for e in exc.errors]

        if p.direction:
            try:
                d = ShotDirection.model_validate(p.direction)
            except ValidationError as exc:
                errors.append(f"{where}: direction: {exc.errors()[0]['msg']}")
                continue
            for mv in d.camera_moves:
                if library.move(mv.id) is None:
                    errors.append(f"{where}: unknown camera move {mv.id!r}")
            for name, section in FIELD_SECTIONS.items():
                value = getattr(d, name)
                if value is not None and library.look(section, value) is None:
                    errors.append(f"{where}: unknown {name.replace('_', ' ')} {value!r}")
    return errors
