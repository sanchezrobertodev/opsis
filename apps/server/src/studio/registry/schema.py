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
    "character_image",  # motion transfer: who performs
    "driving_video",  # motion transfer: whose movement is copied
    "input_image",  # an image a tool works on (segmentation)
]
# mask / mask_video: black-and-white mattes (white = the selected object), kept apart from ordinary media
# model3d: a GLB; splat: a Gaussian-splat world (SPZ or PLY)
MediaKind = Literal["image", "video", "audio", "mask", "mask_video", "model3d", "splat"]
OpTag = Literal[
    "t2v", "i2v", "flf2v", "multi_ref", "multi_shot", "extend", "instruction_edit", "restyle", "reframe",
    "upscale", "interpolate", "bg_remove", "motion_transfer", "object_replace", "lipsync", "avatar",
    "t2i", "i2i", "relight", "add_sound", "trim", "retime", "reverse", "loop", "stabilize",
    "tts", "sfx", "music", "segment", "track",
    "to_3d", "panorama", "world", "depth_to_video",
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
    options: list[str | int] = []
    default: str | int | None = None
    labels: dict[str, str] = {}  # option -> what the user sees, for options that are ids (voices)
    # options come from this director list (registry/director/looks.yaml), filled in when the registry loads
    director: str | None = None


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


class PathsParam(_ParamBase):
    """Motion paths drawn on the start frame: [{"points": [[x, y], ...]}] with x, y in 0-1 of the frame as the
    model will see it (cropped to the output aspect). A path with one point marks something that should hold still.
    Routes turn the paths into whatever the model wants (per-frame pixel tracks for Wan-Move)."""

    type: Literal["paths"]
    min_paths: int = 0
    max_paths: int = 5
    max_points: int = 256
    default: list[Any] | None = None


class PointsParam(_ParamBase):
    """Clicks on an image: [[x, y, label]] with x, y in 0-1 of the image and label 1 (this) or 0 (not this).
    Routes turn them into the provider's shape (pixel points for SAM)."""

    type: Literal["points"]
    min_points: int = 0
    max_points: int = 24
    default: list[Any] | None = None


class ShotsParam(_ParamBase):
    """Several shots rendered in one call (multi-shot): [{"prompt": str, "duration": seconds}] in order, each shot
    optionally with its own `direction`. The clip is as long as its shots together (it sets `duration`). Routes
    send the shots as the provider's shot list (`shot_list`) or as one timed prompt (`timed_prompt`)."""

    type: Literal["shots"]
    min_shots: int = 2
    max_shots: int = 6
    min_shot_s: float = 1
    max_shot_s: float = 15
    max_total_s: float = 15
    # each: every shot may use the model's whole prompt limit (a shot list); shared: the shots share one prompt
    prompt_budget: Literal["each", "shared"] = "each"
    default: list[Any] | None = None


Param = Annotated[
    EnumParam | IntParam | FloatParam | BoolParam | SeedParam | TextParam | PathsParam | PointsParam | ShotsParam,
    Field(discriminator="type"),
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
    min_side: int | None = None  # pixels, both sides of a picture or clip ("720p and up", portrait too)
    min_fps: float | None = None  # for clips (Kling O3 Edit refuses under 23 fps, so 16 fps Wan clips too)


class Mode(_Strict):
    id: str
    label: str
    ops: list[OpTag] = []
    prompt: Literal["required", "optional", "none"] = "required"
    medias: list[MediaSlot] = []

    def slot(self, role: str) -> MediaSlot | None:
        return next((s for s in self.medias if s.role == role), None)


class Rule(_Strict):
    """Cross-param constraint, e.g. Veo: 1080p/4k require an 8 s duration. `max_input_seconds` limits how long an
    input may be while the rule applies (OmniHuman: 30 s of audio at 1080p)."""

    when: dict[str, Scalar | list[Scalar]]
    require: dict[str, list[Scalar]] = {}
    max_input_seconds: dict[MediaRole, float] = {}
    message: str


class PromptSpec(_Strict):
    max_chars: int = 2000
    dialect: str = "default"
    supports_negative: bool = False
    # speaks dialogue written into the prompt, with lip sync (Veo 3.1, Seedance 2.5, Kling 3.0 with audio on)
    dialogue: bool = False


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
    media: dict[str, str] = {}  # per-mode media field names, overriding the route map (APIs differ per endpoint)
    drop: list[str] = []  # canonical params this endpoint doesn't accept (e.g. no seed on reference mode)


class ExtraOutput(_Strict):
    """Another file the same job returns, kept as its own asset (a world's panorama next to its splat)."""

    path: str
    kind: MediaKind
    meta: dict[str, Scalar] = {}


class OutputSpec(_Strict):
    path: str = "output"  # dotted; "a.url | b.url" tries each in turn (Meshy: the rigged GLB when there is one)
    kind: MediaKind = "video"
    multiple: bool = False
    # stored on the asset's metadata, e.g. {projection: equirectangular} marks a 360 panorama for the viewer
    meta: dict[str, Scalar] = {}
    extras: list[ExtraOutput] = []


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
    sunset: date | None = None  # provider retires this route on this date; routing skips it from then on
    eta: str | None = None  # simpleeval expression -> expected seconds, e.g. "70 if quality == 'draft' else 430"
    timeout_s: int = 1800
    charges_on_failure: bool = False
    output: OutputSpec = OutputSpec()
    notes: str | None = None


class Example(_Strict):
    name: str
    mode: str
    prompt: str = ""
    params: dict[str, Any] = {}  # scalars, or structured values such as motion paths
    media: dict[str, str | list[str]] = {}
    smoke: bool = False
    expect: dict[str, Any] = {}


class VirtualEngine(_Strict):
    """One engine a virtual model can run on: which base model and mode serves each of its modes."""

    id: str
    label: str
    modes: dict[str, str]  # virtual mode id -> "base-model-id/mode-id"

    def target(self, mode_id: str) -> tuple[str, str] | None:
        ref = self.modes.get(mode_id)
        if not ref:
            return None
        model_id, _, mode = ref.partition("/")
        return model_id, mode


class VirtualSpec(_Strict):
    """A studio layer over base models (`kind: virtual`). A request names an engine (`engine_param`) and is
    resolved to that engine's base model and mode before anything else happens, so routing, pricing and the
    spend guard are the base model's own. Params named in `director` become shot direction instead."""

    engine_param: str = "engine"
    engines: list[VirtualEngine]
    director: list[str] = []  # ShotDirection fields offered as params (era, lens...)

    def engine(self, engine_id: str | None) -> VirtualEngine | None:
        return next((e for e in self.engines if e.id == engine_id), None)


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
    virtual: VirtualSpec | None = None
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
    # Settings attribute(s) that must be set to use the provider: the API key, plus e.g. a self-hosted endpoint id
    key_setting: str | list[str] | None = None
    concurrency: int = 2
    poll_min_seconds: float | None = None  # provider asks clients not to poll faster than this
    docs: str | None = None
    notes: str | None = None
    # Self-hosted endpoints (our own RunPod workers): the Settings attribute holding the endpoint id, and
    # serverless GPU prices (USD/hour, matched by substring of the GPU name the worker reports).
    endpoint_setting: str | None = None
    gpu_usd_per_hour: dict[str, float] = {}
    # Providers that only take some inputs as web links (RunPod): those media kinds are hosted through the service
    # named by `hosting_setting` (FAL_KEY), so routes that need them are offered only when it's set.
    hosted_kinds: list[MediaKind] = []
    hosting_setting: str | None = None

    def configured(self, settings: Any) -> bool:
        """True when every setting in `key_setting` has a value (providers without one are always configured)."""
        names = [self.key_setting] if isinstance(self.key_setting, str) else (self.key_setting or [])
        return all(getattr(settings, n, "") for n in names)


# ---------------------------------------------------------------- presets (effects gallery)


class PresetField(_Strict):
    """A blank the user fills in; the prompt template refers to it as {key}."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    placeholder: str = ""
    default: str = ""
    required: bool = True
    max_chars: int = 300


class PresetInput(_Strict):
    """Media the user brings. The role must be a slot on the preset's mode."""

    role: MediaRole
    label: str | None = None


class PresetSpec(_Strict):
    """A one-click recipe over one model: prompt template + params + direction + required inputs."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    category: Literal["camera", "effects", "look", "character", "product", "social", "edit"]
    summary: str
    model_id: str
    mode: str
    prompt: str
    fields: list[PresetField] = []
    params: dict[str, Any] = {}
    direction: dict[str, Any] | None = None
    inputs: list[PresetInput] = []
    tags: list[str] = []
    status: Literal["active", "hidden"] = "active"
