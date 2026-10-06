"""Load, validate and lint the YAML registry; keep the last good copy on reload errors."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from studio.registry.schema import ModelSpec, OutputSpec, Pricing, ProviderSpec, Rate, Route, RouteMode

log = logging.getLogger(__name__)

MOCK_PROVIDER = "mock"


@dataclass
class Registry:
    models: dict[str, ModelSpec]
    providers: dict[str, ProviderSpec]
    hash: str
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
    return Route(
        id=f"{spec.id}@mock",
        provider=MOCK_PROVIDER,
        modes={m.id: RouteMode(endpoint=f"mock/{spec.id}/{m.id}") for m in spec.modes},
        pricing=Pricing(basis="per_request", rates=[Rate(usd=0.0)], source="mock"),
        timeout_s=300,
        output=OutputSpec(path="output", kind=kind),
        notes="Generated locally with ffmpeg/Pillow. Costs nothing.",
    )


def _read_yaml_dir(directory: Path) -> list[tuple[Path, Any]]:
    out = []
    if not directory.exists():
        return out
    for path in sorted(directory.rglob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            out.append((path, yaml.safe_load(fh)))
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

    for path, data in _read_yaml_dir(root / "providers"):
        try:
            p = ProviderSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if p.id in providers:
            errors.append(f"{path.name}: duplicate provider id {p.id}")
        providers[p.id] = p

    for path, data in _read_yaml_dir(root / "models"):
        try:
            spec = ModelSpec.model_validate(data)
        except ValidationError as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        if spec.id in models:
            errors.append(f"{path.name}: duplicate model id {spec.id}")
        if not any(r.provider == MOCK_PROVIDER for r in spec.routes):
            spec.routes.append(mock_route(spec))
        models[spec.id] = spec

    from studio.registry.lint import lint  # local import: lint depends on Registry

    reg = Registry(models=models, providers=providers, hash=_digest(root))
    lint_errors, lint_warnings = lint(reg)
    reg.errors = errors + lint_errors
    reg.warnings = lint_warnings
    if reg.errors:
        raise RegistryError(reg.errors)
    return reg


class RegistryHolder:
    """Holds the active registry. A failed reload keeps the previous good one."""

    def __init__(self, root: Path):
        self.root = root
        self._current: Registry | None = None
        self.last_error: list[str] | None = None

    @property
    def current(self) -> Registry:
        if self._current is None:
            self._current = load_registry(self.root)
        return self._current

    def reload(self) -> bool:
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
