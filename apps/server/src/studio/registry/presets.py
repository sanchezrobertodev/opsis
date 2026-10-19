"""Presets: one-click templates over a model (prompt template, params, direction, required inputs).

They live in registry/presets/*.yaml next to the models they use. Rendering fills the template from the
user's answers and returns an ordinary generation request, so presets go through the same planning,
spend guard and routing as anything typed into Create.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from studio.registry.schema import PresetField, PresetSpec, TemplateSpec
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


def _answers(fields: list[PresetField], values: dict[str, str]) -> dict[str, str]:
    """Each field's tidied answer (or default). Missing required ones are an error."""
    errors: list[str] = []
    filled: dict[str, str] = {}
    for f in fields:
        value = " ".join(str(values.get(f.key) or "").split())[: f.max_chars] or f.default
        if not value and f.required:
            errors.append(f"{f.label} is required")
        filled[f.key] = value
    if errors:
        raise PresetError(errors)
    return filled


def _fill(template: str, filled: dict[str, str]) -> str:
    text = PLACEHOLDER.sub(lambda m: filled.get(m.group(1), ""), template)
    # optional blanks left empty shouldn't leave stray punctuation or double spaces behind
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    return " ".join(text.split()).strip(" ,")


def render_prompt(preset: PresetSpec, values: dict[str, str]) -> str:
    """Fill {key} blanks. Unknown keys are ignored; missing required ones are an error."""
    return _fill(preset.prompt, _answers(preset.fields, values))


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
    from studio.director.compiler import ShotDirection, unknown_ids
    from studio.director.library import get_library

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
            errors += [f"{where}: {e}" for e in unknown_ids(d, library)]
    return errors


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _filled(value: Any, filled: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _fill(value, filled) if PLACEHOLDER.search(value) else value
    if isinstance(value, dict):
        return {k: _filled(v, filled) for k, v in value.items()}
    if isinstance(value, list):
        return [_filled(v, filled) for v in value]
    return value


def render_template(template: TemplateSpec, values: dict[str, str], media: dict[str, str]) -> dict[str, Any]:
    """The run this template starts with these answers and inputs (media: input key -> asset id)."""
    missing = [i.label for i in template.inputs if not media.get(i.key)]
    if missing:
        raise PresetError([f"add {', '.join(missing).lower()}"])
    inp = _filled(template.input, _answers(template.fields, values))
    inp |= {i.key: media[i.key] for i in template.inputs}
    return {"recipe": template.recipe, "input": inp, "title": template.name}


def lint_templates(reg: Registry) -> list[str]:
    from studio.pipelines.recipes import get_recipe  # local import: recipes depend on the registry

    errors: list[str] = []
    for t in reg.templates.values():
        where = f"template {t.id}"
        recipe = get_recipe(t.recipe)
        if recipe is None:
            errors.append(f"{where}: unknown recipe {t.recipe!r}")
            continue
        keys = [f.key for f in t.fields]
        used = {k for s in _strings(t.input) for k in placeholders(s)}
        if len(keys) != len(set(keys)):
            errors.append(f"{where}: duplicate field keys")
        if unknown := used - set(keys):
            errors.append(f"{where}: input uses undeclared fields {sorted(unknown)}")
        if unused := set(keys) - used:
            errors.append(f"{where}: fields never used in the input {sorted(unused)}")
        if clash := {i.key for i in t.inputs} & set(t.input):
            errors.append(f"{where}: inputs overwrite fixed input {sorted(clash)}")
        sample = render_template(t, {f.key: f.placeholder or f.label for f in t.fields},
                                 {i.key: "00000000-0000-7000-8000-000000000000" for i in t.inputs})
        try:
            inp = recipe.Input.model_validate(sample["input"])
        except ValidationError as exc:
            errors += [f"{where}: {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            continue
        errors += [f"{where}: {e}" for e in recipe.validate(inp, reg)]
    return errors
