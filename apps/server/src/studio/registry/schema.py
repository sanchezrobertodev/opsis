"""Registry schema: the single source of truth for models, routes, params and pricing.

YAML files under registry/ are validated against these models. Every studio form, cost estimate,
route choice and provider payload is derived from them, so model-specific behaviour lives in data,
not in core code.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MediaRole = Literal[
    "start_image",
    "end_image",
    "image_references",
    "video_references",
    "audio_references",
    "mask",
    "input_video",
    "input_audio",
    "control_video",
    "style_image",
    "replacement_image",
]
MediaKind = Literal["image", "video", "audio"]
OpTag = Literal[
    "t2v", "i2v", "flf2v", "multi_ref", "multi_shot", "extend", "instruction_edit", "restyle", "reframe",
    "upscale", "interpolate", "bg_remove", "motion_transfer", "object_replace", "lipsync", "avatar",
    "t2i", "i2i",
]
OutputType = Literal["video", "image", "audio", "3d", "lora", "mask", "text"]
Scalar = str | int | float | bool


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- params


class _ParamBase(_Strict):
    name: str
    label: str | None = None
    help: str | None = None
    group: Literal["basic", "advanced"] = "basic"
    modes: list[str] | None = None  # None = every mode
    visible_if: dict[str, Scalar | list[Scalar]] | None = None
    ui: str | None = None  # slider | segmented | aspect | textarea ...


class EnumParam(_ParamBase):
    type: Literal["enum"]
    options: list[str | int]
    default: str | int | None = None


class IntParam(_ParamBase):
    type: Literal["int"]
    min: int
    max: int
    step: int = 1
    default: int | None = None


class FloatParam(_ParamBase):
    type: Literal["float"]
    min: float
    max: float
    step: float = 0.01
    default: float | None = None


class BoolParam(_ParamBase):
    type: Literal["bool"]
    default: bool = False


class SeedParam(_ParamBase):
    type: Literal["seed"]
    default: int | None = None


class TextParam(_ParamBase):
    type: Literal["text"]
    max_chars: int | None = None
    default: str | None = None


Param = Annotated[
    EnumParam | IntParam | FloatParam | BoolParam | SeedParam | TextParam, Field(discriminator="type")
]


# ---------------------------------------------------------------- modes / media


class MediaSlot(_Strict):
    role: MediaRole
    kinds: list[MediaKind]
    label: str | None = None
    required: bool = False
    min: int = 0
    max: int = 1
    max_duration_s: float | None = None
    max_mb: float | None = None


class Mode(_Strict):
    id: str
    label: str
    ops: list[OpTag] = []
    prompt: Literal["required", "optional", "none"] = "required"
    medias: list[MediaSlot] = []

    def slot(self, role: str) -> MediaSlot | None:
        return next((s for s in self.medias if s.role == role), None)


class Rule(_Strict):
    """Cross-param constraint, e.g. Veo: 1080p/4k require an 8 s duration."""

    when: dict[str, Scalar | list[Scalar]]
    require: dict[str, list[Scalar]]
    message: str


class PromptSpec(_Strict):
    max_chars: int = 2000
    dialect: str = "default"
    supports_negative: bool = False


class DurationSpec(_Strict):
    min: float
    max: float
    default: float


# ---------------------------------------------------------------- pricing / routes


class Rate(_Strict):
    when: dict[str, Scalar | list[Scalar]] = {}
    usd: float
    per: float = 1.0


class Multiplier(_Strict):
    when: dict[str, Scalar | list[Scalar]]
    factor: float


PricingBasis = Literal[
    "per_output_second", "per_image", "per_request", "per_video_token", "per_1k_chars", "per_gpu_second", "formula"
]


class Pricing(_Strict):
    basis: PricingBasis
    rates: list[Rate] = []
    multipliers: list[Multiplier] = []
    quantity: str | None = None  # simpleeval expression overriding the basis quantity
    formula: str | None = None  # basis=formula: full USD expression
    minimum_usd: float = 0.0
    verified: date | None = None
    source: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Pricing:
        if self.basis == "formula" and not self.formula:
            raise ValueError("basis=formula needs `formula`")
        if self.basis != "formula" and not self.rates:
            raise ValueError("pricing needs at least one rate")
        return self


class ParamMapEntry(_Strict):
    to: str | None = None  # None = drop the param for this route
    transform: str | None = None  # str | int | float | enum_map | seconds_to_frames | bool_to_str
    args: dict[str, Any] = {}


class ParamMap(_Strict):
    prompt: str | None = "prompt"
    negative_prompt: str | None = "negative_prompt"
    params: dict[str, str | ParamMapEntry] = {}
    media: dict[str, str] = {}
    passthrough: bool = True  # unmapped params are sent under their canonical name


class RouteMode(_Strict):
    endpoint: str
    fixed: dict[str, Any] = {}


class OutputSpec(_Strict):
    path: str = "output"
    kind: MediaKind = "video"
    multiple: bool = False


class Route(_Strict):
    id: str
    provider: str
    enabled: bool = True
    modes: dict[str, RouteMode]
    map: ParamMap = ParamMap()
    transform: str | None = None  # dotted path to a payload hook for odd shapes
    constraints: dict[str, dict[str, Any]] = {}  # param -> {options|min|max}
    unsupported_params: list[str] = []
    pricing: Pricing
    timeout_s: int = 1800
    charges_on_failure: bool = False
    output: OutputSpec = OutputSpec()
    notes: str | None = None


class Example(_Strict):
    name: str
    mode: str
    prompt: str = ""
    params: dict[str, Scalar] = {}
    media: dict[str, str | list[str]] = {}
    smoke: bool = False
    expect: dict[str, Any] = {}


class ModelSpec(_Strict):
    id: str
    name: str
    kind: Literal["model", "virtual", "recipe"] = "model"
    family: str
    vendor: str
    description: str = ""
    output_type: OutputType
    status: Literal["active", "preview", "deprecated", "shutdown"] = "active"
    license_tier: Literal["commercial_api", "open_permissive", "open_restricted", "non_commercial"] = "commercial_api"
    tags: list[str] = []
    prompt: PromptSpec = PromptSpec()
    aspect_ratios: list[str] = []
    duration: DurationSpec | None = None
    parameters: list[Param] = []
    rules: list[Rule] = []
    modes: list[Mode]
    elements: dict[str, Any] | None = None
    camera: dict[str, Any] | None = None
    wraps: list[str] = []
    recipe_id: str | None = None
    routes: list[Route] = []
    examples: list[Example] = []

    def mode(self, mode_id: str) -> Mode | None:
        return next((m for m in self.modes if m.id == mode_id), None)

    def param(self, name: str) -> Param | None:
        return next((p for p in self.parameters if p.name == name), None)

    def params_for(self, mode_id: str) -> list[Param]:
        return [p for p in self.parameters if p.modes is None or mode_id in p.modes]

    def route(self, route_id: str) -> Route | None:
        return next((r for r in self.routes if r.id == route_id), None)


class ProviderSpec(_Strict):
    id: str
    name: str
    kind: Literal["aggregator", "direct", "selfhosted", "local", "mock"]
    adapter: str  # adapter key in studio.providers.factory
    key_setting: str | None = None  # Settings attribute holding the API key
    concurrency: int = 2
    docs: str | None = None
    notes: str | None = None
