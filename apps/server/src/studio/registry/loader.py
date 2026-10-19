"""Load, validate and lint the YAML registry; keep the last good copy on reload errors."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from studio.registry.schema import (
    EnumParam,
    ModelSpec,
    OutputSpec,
    PresetSpec,
    Pricing,
    ProviderSpec,
    Rate,
    Route,
    RouteMode,
    TemplateSpec,
)

log = logging.getLogger(__name__)

MOCK_PROVIDER = "mock"


@dataclass
class Registry:
    models: dict[str, ModelSpec]
    providers: dict[str, ProviderSpec]
    hash: str
    presets: dict[str, PresetSpec] = field(default_factory=dict)
    templates: dict[str, TemplateSpec] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def model(self, model_id: str) -> ModelSpec | None:
        return self.models.get(model_id)


class RegistryError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def mock_route(spec: ModelSpec) -> Route:
    """Every model gets a $0 mock route so the whole product works without spending."""
    kind = spec.output_type if spec.output_type in ("video", "image", "audio") else "video"
    if spec.output_type == "lora":  # trainers return weights, not media
        kind = "lora"
    if spec.output_type in ("3d", "splat"):  # mock the file kind the real routes return
        kinds = {r.output.kind for r in spec.routes}
        kind = kinds.pop() if len(kinds) == 1 else "model3d"
    if spec.output_type == "text":  # speech to text: a transcript
        kind = "subtitle"
    if spec.output_type == "mask":  # one mode segments an image, another tracks a clip
        kinds = {r.output.kind for r in spec.routes}
        kind = "mask_video" if kinds == {"mask_video"} else "mask"
    # same metadata and extra files as the real route (a world's panorama), so the product sees the same shapes
    real = spec.routes[0].output if spec.routes else OutputSpec()
    return Route(
        id=f"{spec.id}@mock",
        provider=MOCK_PROVIDER,
        modes={m.id: RouteMode(endpoint=f"mock/{spec.id}/{m.id}") for m in spec.modes},
        pricing=Pricing(basis="per_request", rates=[Rate(usd=0.0)], source="mock"),
        timeout_s=300,
        output=OutputSpec(path="output", kind=kind, meta=real.meta, extras=real.extras),
        notes="Generated locally with ffmpeg/Pillow. Costs nothing.",
    )


def _fill_director_options(models: dict[str, ModelSpec], root: Path, errors: list[str]) -> None:
    """Enum params with `director: <list>` take their options and labels from the director vocabulary."""
    if not any(isinstance(p, EnumParam) and p.director for s in models.values() for p in s.parameters):
        return
    from studio.director.library import (
        load_library,  # local import: the director package imports the registry
    )

    try:
        lib = load_library(root / "director")
    except Exception as exc:  # a broken vocabulary file is reported like any other registry error
        errors.append(f"director: {exc}")
        return
    for spec in models.values():
        for p in spec.parameters:
            if isinstance(p, EnumParam) and p.director:
                items = getattr(lib, p.director, None)
                if not isinstance(items, list):
                    errors.append(f"model {spec.id}: param {p.name} names unknown director list {p.director!r}")
                    continue
                p.options = [i.id for i in items]
                p.labels = {i.id: i.label for i in items}


def _fill_field_choices(models: dict[str, ModelSpec], cards: list[Any], errors: list[str]) -> None:
    """Preset and template fields with `choices: model.param` take that enum param's options and labels."""
    for card in cards:
        for f in card.fields:
            if not f.choices:
                continue
            model_id, _, name = f.choices.rpartition(".")
            spec = models.get(model_id)
            p = spec.param(name) if spec else None
            if not isinstance(p, EnumParam):
                errors.append(f"{card.id}: field {f.key} chooses from {f.choices!r}, which isn't an enum param")
                continue
            f.options = [str(o) for o in p.options]
            f.option_labels = {str(k): v for k, v in (p.labels or {}).items()}
            f.open = p.open


def _read_yaml_dir(directory: Path, errors: list[str]) -> list[tuple[Path, Any]]:
    out = []
    if not directory.exists():
        return out
    for path in sorted(directory.rglob("*.yaml")):
        try:
            with path.open(encoding="utf-8") as fh:
                out.append((path, yaml.safe_load(fh)))
        except yaml.YAMLError as exc:  # a file mid-edit: report it, keep loading the rest
            errors.append(f"{path.relative_to(directory.parent).as_posix()}: not valid YAML ({exc})")
    return out


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.yaml")):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def load_registry(root: Path) -> Registry:
    errors: list[str] = []
    providers: dict[str, ProviderSpec] = {}
    models: dict[str, ModelSpec] = {}

    for path, data in _read_yaml_dir(root / "providers", errors):
        try:
            p = ProviderSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if p.id in providers:
            errors.append(f"{path.name}: duplicate provider id {p.id}")
        providers[p.id] = p

    for path, data in _read_yaml_dir(root / "models", errors):
        try:
            spec = ModelSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        if spec.id in models:
            errors.append(f"{path.name}: duplicate model id {spec.id}")
        # a virtual model runs on its engines' routes (mock included), never its own
        if spec.kind != "virtual" and not any(r.provider == MOCK_PROVIDER for r in spec.routes):
            spec.routes.append(mock_route(spec))
        models[spec.id] = spec
    _fill_director_options(models, root, errors)

    presets: dict[str, PresetSpec] = {}
    for path, data in _read_yaml_dir(root / "presets", errors):
        try:
            preset = PresetSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        if preset.id in presets:
            errors.append(f"{path.name}: duplicate preset id {preset.id}")
        presets[preset.id] = preset

    templates: dict[str, TemplateSpec] = {}
    for path, data in _read_yaml_dir(root / "templates", errors):
        try:
            template = TemplateSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        if template.id in templates:
            errors.append(f"{path.name}: duplicate template id {template.id}")
        templates[template.id] = template

    _fill_field_choices(models, [*presets.values(), *templates.values()], errors)

    from studio.registry.lint import lint  # local import: lint depends on Registry
    from studio.registry.presets import lint_presets, lint_templates

    reg = Registry(models=models, providers=providers, hash=_digest(root), presets=presets, templates=templates)
    lint_errors, lint_warnings = lint(reg)
    reg.errors = errors + lint_errors + lint_presets(reg) + lint_templates(reg)
    reg.warnings = lint_warnings
    if reg.errors:
        raise RegistryError(reg.errors)
    return reg


class RegistryHolder:
    """Holds the active registry. A failed reload keeps the previous good one.

    Every process (API, worker) notices edited YAML on its own: `current` compares the files' sizes and
    modification times at most once per `check_every` seconds and reloads when they changed, so a model the
    API accepts is never unknown to the worker.
    """

    def __init__(self, root: Path, check_every: float = 1.0):
        self.root = root
        self.check_every = check_every
        self._current: Registry | None = None
        self._stamp: tuple | None = None
        self._checked = 0.0
        self.last_error: list[str] | None = None

    def _files(self) -> tuple:
        return tuple(sorted((p.relative_to(self.root).as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
                            for p in self.root.rglob("*.yaml")))

    @property
    def current(self) -> Registry:
        if self._current is None:
            self._stamp = self._files()
            self._current = load_registry(self.root)
            self._checked = time.monotonic()
        elif time.monotonic() - self._checked >= self.check_every:
            self._checked = time.monotonic()
            stamp = self._files()
            if stamp != self._stamp:
                self._stamp = stamp
                self.reload()
        return self._current

    def reload(self) -> bool:
        self._stamp = self._files()
        try:
            self._current = load_registry(self.root)
        except RegistryError as exc:
            self.last_error = exc.errors
            log.error("registry reload failed; keeping last good registry:\n%s", exc)
            return False
        self.last_error = None
        log.info("registry reloaded (hash %s, %d models)", self._current.hash, len(self._current.models))
        return True


_holder: RegistryHolder | None = None


def get_registry_holder() -> RegistryHolder:
    global _holder
    if _holder is None:
        from studio.config import get_settings

        _holder = RegistryHolder(get_settings().resolve_registry_dir())
    return _holder


def get_registry() -> Registry:
    return get_registry_holder().current
