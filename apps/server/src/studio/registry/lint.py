"""Registry lint: structural checks that schema validation alone can't express."""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

from studio.registry.schema import EnumParam, FloatParam, IntParam, ModelSpec, Route

if TYPE_CHECKING:
    from studio.registry.loader import Registry


COMFY_ADAPTERS = {"comfy_local", "runpod_comfy"}  # routes whose endpoint names a workflows/comfy template


def _check_workflows(spec: ModelSpec, route: Route, rwhere: str) -> list[str]:
    """A ComfyUI route's template must exist, and every field the route sends must have an input in it."""
    from studio.config import get_settings
    from studio.providers.comfy_workflows import load_template

    errors: list[str] = []
    for mode_id, rm in route.modes.items():
        try:
            template = load_template(get_settings().comfyui_workflows_dir, rm.endpoint)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"{rwhere}: {exc}")
            continue
        mode = spec.mode(mode_id)
        sent = {route.map.prompt} if route.map.prompt and mode and mode.prompt != "none" else set()
        for p in spec.params_for(mode_id):
            entry = route.map.params.get(p.name)
            target = entry if isinstance(entry, str) else (entry.to if entry else (p.name if route.map.passthrough else None))
            if target:
                sent.add(target)
        for slot in mode.medias if mode else []:
            sent.add(rm.media.get(slot.role) or route.map.media.get(slot.role, slot.role))
        sent |= set(rm.fixed)
        for key in sorted(k for k in sent if k not in template.map.inputs):
            errors.append(f"{rwhere}: workflow {rm.endpoint} has no input for {key!r} (mode {mode_id})")
    return errors


def lint(reg: Registry) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    route_ids: set[str] = set()

    for spec in reg.models.values():
        where = f"model {spec.id}"
        mode_ids = {m.id for m in spec.modes}
        if len(mode_ids) != len(spec.modes):
            errors.append(f"{where}: duplicate mode ids")

        for p in spec.parameters:
            if p.modes and not set(p.modes) <= mode_ids:
                errors.append(f"{where}: param {p.name} references unknown modes {set(p.modes) - mode_ids}")
            if isinstance(p, EnumParam) and p.default is not None and p.default not in p.options:
                errors.append(f"{where}: param {p.name} default {p.default!r} not in options")
            if isinstance(p, EnumParam) and (extra := set(p.labels) - {str(o) for o in p.options}):
                errors.append(f"{where}: param {p.name} labels unknown options {sorted(extra)}")
            if isinstance(p, (IntParam, FloatParam)) and p.default is not None and not p.min <= p.default <= p.max:
                errors.append(f"{where}: param {p.name} default out of range")
        param_names = {p.name for p in spec.parameters}

        for mode in spec.modes:
            roles = [s.role for s in mode.medias]
            if len(roles) != len(set(roles)):
                errors.append(f"{where}: mode {mode.id} repeats a media role")
            live = [r for r in spec.routes if r.provider != "mock" and mode.id in r.modes]
            if not live and spec.status != "shutdown":
                warnings.append(f"{where}: mode {mode.id} has no live route (mock only)")

        for route in spec.routes:
            rwhere = f"{where} route {route.id}"
            if route.id in route_ids:
                errors.append(f"{rwhere}: duplicate route id")
            route_ids.add(route.id)
            if route.provider not in reg.providers:
                errors.append(f"{rwhere}: unknown provider {route.provider!r}")
            if not set(route.modes) <= mode_ids:
                errors.append(f"{rwhere}: unknown modes {set(route.modes) - mode_ids}")
            for name in route.map.params:
                if name not in param_names:
                    errors.append(f"{rwhere}: map references unknown param {name!r}")
            all_roles = {s.role for m in spec.modes for s in m.medias}
            for role in route.map.media:
                if role not in all_roles:
                    errors.append(f"{rwhere}: map references unknown media role {role!r}")
            if route.sunset and route.enabled:
                days = (route.sunset - date.today()).days
                if days <= 30:
                    warnings.append(f"{rwhere}: provider retires it on {route.sunset} ({max(days, 0)} days)")
            for mode_id, rm in route.modes.items():
                for name in re.findall(r"\{(\w+)\}", rm.endpoint):
                    if name not in param_names:
                        errors.append(f"{rwhere}: endpoint {rm.endpoint!r} uses unknown param {name!r}")
                mode = spec.mode(mode_id)
                for role in rm.media:
                    if mode and not mode.slot(role):
                        errors.append(f"{rwhere}: mode {mode_id} maps unknown media role {role!r}")
            provider = reg.providers.get(route.provider)
            if provider and provider.adapter in COMFY_ADAPTERS:
                errors.extend(_check_workflows(spec, route, rwhere))
            if route.provider != "mock":
                if route.pricing.verified is None:
                    errors.append(f"{rwhere}: pricing needs a `verified` date")
                if spec.status == "shutdown" and route.enabled:
                    errors.append(f"{rwhere}: shut-down model must not have enabled routes")

        for ex in spec.examples:
            if ex.mode not in mode_ids:
                errors.append(f"{where}: example {ex.name} uses unknown mode {ex.mode}")
        if not spec.examples:
            warnings.append(f"{where}: no examples (contract/smoke tests skip it)")

    return errors, warnings
