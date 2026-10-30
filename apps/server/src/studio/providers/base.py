"""Provider adapter interface.

Adapters only own transport: staging inputs, submit/poll/cancel, and downloading outputs. Payload
building (registry/mapping.py), pricing (registry/pricing.py) and routing (registry/routing.py) are
shared and data-driven, so adding a model never needs adapter code unless the protocol differs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from studio.domain.enums import ErrorClass
from studio.registry.schema import Route

PollStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


@dataclass
class ResolvedMedia:
    """An input asset resolved to a local file, ready for staging."""

    role: str
    asset_id: str
    path: Path
    mime: str
    kind: str


@dataclass
class SubmitResult:
    job_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderOutput:
    index: int
    kind: str  # video | image | audio
    url: str | None = None
    ref: str | None = None  # provider handle when there is no URL (mock job id, Google file uri…)
    mime: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    data: Any = None  # the provider's value itself, when the route decodes it into a file (OutputSpec.decode)


@dataclass
class PollResult:
    status: PollStatus
    progress: float | None = None
    queue_position: int | None = None
    outputs: list[ProviderOutput] = field(default_factory=list)
    error_class: ErrorClass | None = None
    error_message: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    def __init__(self, error_class: ErrorClass, message: str):
        super().__init__(message)
        self.error_class = error_class
        self.message = message


class AmbiguousSubmitError(ProviderError):
    """The provider may or may not have accepted the job (timeout mid-submit). Never auto-resubmit."""

    def __init__(self, message: str):
        super().__init__(ErrorClass.transient, message)


class ProviderAdapter(ABC):
    key: str

    @abstractmethod
    async def stage_inputs(self, route: Route, media: list[ResolvedMedia]) -> dict[str, Any]:
        """Return media role -> provider-ready reference(s) (URL, file id, base64 data URI...)."""

    @abstractmethod
    async def submit(self, route: Route, endpoint: str, payload: dict[str, Any], *, client_ref: str) -> SubmitResult:
        ...

    @abstractmethod
    async def poll(self, route: Route, job_id: str) -> PollResult: ...

    @abstractmethod
    async def cancel(self, route: Route, job_id: str) -> bool: ...

    async def download(self, route: Route, output: ProviderOutput, dest: Path, workdir: Path) -> str:
        """Write the output to `dest`; return its MIME type. By default its link is fetched."""
        from studio.providers.common import best_mime, download_url  # common imports this module

        content_type = await download_url(output.url or "", dest)
        return best_mime(output.mime, content_type, kind=output.kind)

    async def reachable(self) -> bool:
        """For local engines: can this machine run jobs right now? Remote APIs are assumed up (health
        tracking and the circuit breaker handle outages)."""
        return True

    async def unavailable(self, route: Route, mode: str) -> str | None:
        """For local engines that are up: why this route's mode can't run here (too little GPU memory, missing model
        files), or None when it can."""
        return None

    def classify(self, exc: BaseException) -> ErrorClass:
        if isinstance(exc, ProviderError):
            return exc.error_class
        return ErrorClass.transient
