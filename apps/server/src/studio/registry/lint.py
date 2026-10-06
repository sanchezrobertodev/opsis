"""Registry lint: structural checks that schema validation alone can't express."""

from __future__ import annotations

from typing import TYPE_CHECKING

from studio.registry.schema import EnumParam, FloatParam, IntParam

if TYPE_CHECKING:
    from studio.registry.loader import Registry


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
