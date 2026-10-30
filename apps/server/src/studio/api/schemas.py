"""API request/response models. These define the OpenAPI schema the web client is generated from."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.director.compiler import ShotDirection
from studio.director.library import CameraMove, EmotionItem, LookItem
from studio.registry.schema import (
    DurationSpec,
    MediaSlot,
    Mode,
    Param,
    PresetField,
    PromptSpec,
    Rule,
    TemplateInput,
    VirtualSpec,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ registry


class ModeSummary(ApiModel):
    id: str
    label: str
    ops: list[str]


class ModelSummary(ApiModel):
    id: str
    name: str
    kind: str = Field("model", description="model, or virtual: a studio layer that runs on other models")
    family: str
    vendor: str
    description: str
    output_type: str
    status: str
    license_tier: str
    tags: list[str]
    modes: list[ModeSummary]
    from_price_usd: float | None = Field(None, description="Cheapest live estimate with default params")
    runnable: bool = Field(description="At least one route can run right now (mock counts)")
    runs_on: Literal["local", "cloud_gpu", "hosted"] = Field(
        "hosted", description="Where it runs: this computer, your own cloud GPU endpoint, or a hosted API")


class RouteSummary(ApiModel):
    id: str
    provider: str
    provider_name: str
    is_mock: bool
    enabled: bool
    modes: list[str]
    pricing_basis: str
    pricing_verified: str | None
    pricing_source: str | None
    notes: str | None


class ModelDetail(ApiModel):
    id: str
    name: str
    kind: str
    family: str
    vendor: str
    description: str
    output_type: str
    status: str
    license_tier: str
    tags: list[str]
    prompt: PromptSpec
    aspect_ratios: list[str]
    duration: DurationSpec | None
    parameters: list[Param]
    rules: list[Rule]
    modes: list[Mode]
    elements: dict[str, Any] | None
    routes: list[RouteSummary]
    virtual: VirtualSpec | None = Field(None, description="Engines a virtual model runs on, and its director params")


class RegistryOut(ApiModel):
    hash: str
    provider_mode: Literal["mock", "mixed", "live"]
    models: list[ModelSummary]
    warnings: list[str]


# ------------------------------------------------------------------ generations


class MediaInput(ApiModel):
    role: str
    asset_id: uuid.UUID


class GenerationCreate(ApiModel):
    model_id: str
    mode: str
    prompt: str = ""
    negative_prompt: str | None = None
    params: dict[str, Any] = {}
    media: list[MediaInput] = []
    route_policy: str = "cheapest_healthy"
    confirm_cost_usd: float | None = Field(None, description="Echo the estimate to confirm an expensive run")
    group_id: uuid.UUID | None = None
    parent_generation_id: uuid.UUID | None = None
    direction: ShotDirection | None = Field(None, description="Camera moves and cinema look, compiled into the prompt")
    project_id: uuid.UUID | None = Field(None, description="A Cinema Studio project this take belongs to")
    shot_key: str | None = Field(None, max_length=80, description="The project's shot this take is for")


class CandidateOut(ApiModel):
    route_id: str
    provider: str
    provider_name: str | None = None
    provider_kind: str | None = None
    account: str | None = Field(None, description="The billing account whose balance applies (RunPod for our endpoint)")
    eta_seconds: float | None = None
    eligible: bool
    reason: str | None
    estimate_usd: float | None
    pricing_basis: str | None
    is_mock: bool


class EstimateOut(ApiModel):
    model_id: str = Field(description="The model that runs (a virtual model's engine)")
    mode: str
    via: dict[str, Any] | None = Field(None, description="The virtual model asked for, and its engine")
    chosen_route_id: str | None
    estimate_usd: float | None
    live_reference: CandidateOut | None
    candidates: list[CandidateOut]
    warnings: list[str]
    params: dict[str, Any]
    compiled_prompt: str | None = None


class DirectorLibraryOut(ApiModel):
    moves: list[CameraMove]
    shot_sizes: list[LookItem]
    angles: list[LookItem]
    lenses: list[LookItem]
    focal_lengths: list[LookItem]
    apertures: list[LookItem]
    camera_bodies: list[LookItem]
    eras: list[LookItem]
    lighting: list[LookItem]
    light_sources: list[LookItem]
    light_positions: list[LookItem]
    light_colors: list[LookItem]
    palettes: list[LookItem]
    genres: list[LookItem]
    pacing: list[LookItem]
    emotions: list[EmotionItem]


class CompileIn(ApiModel):
    model_id: str
    prompt: str = ""
    direction: ShotDirection | None = None
    mode: str | None = Field(None, description="Resolve @mentions as this mode would (reference slots, tokens)")
    media: list[MediaInput] = []


class CompileOut(ApiModel):
    prompt: str
    warnings: list[str]
    dialect: str
    max_chars: int


class AssetOut(ApiModel):
    id: uuid.UUID
    kind: str
    status: str
    source: str
    mime: str
    url: str
    thumb_url: str | None
    poster_url: str | None
    filmstrip_url: str | None
    filmstrip_frames: int | None
    proxy_url: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    has_audio: bool | None
    size_bytes: int | None
    title: str | None
    tags: list[str]
    favorite: bool
    generation_id: uuid.UUID | None
    output_index: int | None
    metadata: dict[str, Any]
    created_at: datetime


class GenerationOut(ApiModel):
    id: uuid.UUID
    model_id: str
    mode: str
    output_type: str
    status: str
    status_reason: str | None
    progress: float
    progress_is_estimated: bool
    request: dict[str, Any]
    routing: dict[str, Any]
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    error_code: str | None
    error_message: str | None
    group_id: uuid.UUID | None
    parent_generation_id: uuid.UUID | None
    project_id: uuid.UUID | None = None
    shot_key: str | None = None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    assets: list[AssetOut] = []


class GenerationPage(ApiModel):
    items: list[GenerationOut]
    next_cursor: str | None


class GenerationRemoved(ApiModel):
    files_removed: int = Field(description="Outputs whose files were deleted from disk")
    kept: list[str] = Field(description="What still uses the outputs that were kept, e.g. 'the character Mara'")


class AssetPage(ApiModel):
    items: list[AssetOut]
    next_cursor: str | None


class FrameIn(ApiModel):
    at: Literal["first", "last"] | float = Field("last", description="'first', 'last', or seconds into the clip")


class AssetPatch(ApiModel):
    title: str | None = None
    tags: list[str] | None = Field(None, description="Replace the tags")
    add_tags: list[str] | None = Field(None, description="Add tags, keeping the others")
    favorite: bool | None = None


class LineageEdge(ApiModel):
    parent_asset_id: uuid.UUID
    child_asset_id: uuid.UUID
    relation: str
    generation_id: uuid.UUID | None


class AssetLineageOut(ApiModel):
    parents: list[LineageEdge]
    children: list[LineageEdge]


# ------------------------------------------------------------------ errors


class ValidationProblem(ApiModel):
    code: Literal["invalid_request"] = "invalid_request"
    errors: list[str]
    candidates: list[CandidateOut] = []


class ConfirmationProblem(ApiModel):
    code: Literal["confirmation_required"] = "confirmation_required"
    message: str
    estimate_usd: float
    route_id: str
    threshold_usd: float


# ------------------------------------------------------------------ budget / health


class BudgetOut(ApiModel):
    today_usd: float
    month_usd: float
    reserved_usd: float
    daily_cap_usd: float
    monthly_cap_usd: float
    confirm_over_usd: float
    live_enabled: bool
    provider_mode: Literal["mock", "mixed", "live"] = Field(description="In effect: the Settings switch within .env's")
    provider_modes: list[str] = Field(description="What the switch may choose: PROVIDER_MODE in .env and safer")
    llm_mode: Literal["mock", "live"] = Field(description="Claude's mode in effect, the same way")
    llm_modes: list[str]
    live_route_allowlist: list[str] = Field(description="Routes that run for real in mixed mode (LIVE_ROUTE_ALLOWLIST)")


class SpendEntryOut(ApiModel):
    id: uuid.UUID
    created_at: datetime
    amount_usd: float
    entry_type: str
    provider: str | None
    route_id: str | None
    estimated: bool
    kind: Literal["generation", "claude", "other"]
    label: str = Field(description="What it paid for: the take's prompt, or the Claude task")
    model_id: str | None = None
    generation_id: uuid.UUID | None = None


class SpendBreakdown(ApiModel):
    provider: str
    usd: float
    count: int


class ReconcileOut(ApiModel):
    provider: str
    status: Literal["ok", "unavailable", "error", "skipped"]
    checked: int = Field(description="Charges (fal) or hours (our RunPod endpoints) compared with the bill")
    billed_usd: float
    charged_usd: float
    booked_usd: float = Field(description="Corrections booked by this check (the bill minus what was charged)")
    outside_usd: float = Field(description="Billed for requests the studio didn't make (another app on the key); "
                                           "not booked")
    detail: str | None = None


class BalanceOut(ApiModel):
    provider: str
    label: str
    status: Literal["ok", "unavailable", "no_api", "error"] = Field(
        description="ok: read now; unavailable: the key can't read it; no_api: see the billing page; error: unreachable")
    link: str = Field(description="The provider's billing page")
    amount: float | None = None
    unit: Literal["usd", "credits"] | None = None
    usd: float | None = Field(None, description="The amount in dollars, when credits have a known price")
    detail: str | None = None
    checked_at: datetime


class SpendGroup(ApiModel):
    key: str
    label: str
    usd: float
    count: int
    estimated_usd: float = Field(description="How much of `usd` is still the studio's estimate (no bill read yet)")
    checked_usd: float = Field(0.0, description="How much of `usd` was checked against the provider's bill")


class SpendBreakdownOut(ApiModel):
    period: Literal["today", "month", "all"]
    total_usd: float
    estimated_usd: float = Field(0.0, description="Still the studio's estimate")
    checked_usd: float = Field(0.0, description="Checked against a provider's bill (the rest the provider reported "
                                                "as it ran, or is still an estimate)")
    features: list[SpendGroup] = Field(description="By studio feature, most spent first")
    models: list[SpendGroup] = Field(description="By model (Claude by its model), most spent first")


class SpendHistoryOut(ApiModel):
    entries: list[SpendEntryOut]
    by_provider: list[SpendBreakdown] = Field(description="This month's charges per provider")


class BudgetUpdate(ApiModel):
    daily_cap_usd: float | None = Field(None, ge=0)
    monthly_cap_usd: float | None = Field(None, ge=0)
    confirm_over_usd: float | None = Field(None, ge=0)
    live_enabled: bool | None = None
    provider_mode: Literal["mock", "mixed", "live"] | None = Field(None, description="Within PROVIDER_MODE in .env")
    llm_mode: Literal["mock", "live"] | None = Field(None, description="Within LLM_MODE in .env")


class LocalEngineOut(ApiModel):
    configured: bool
    online: bool
    url: str | None
    gpu: str | None = None
    vram_total_gb: float | None = None
    vram_free_gb: float | None = None
    comfyui_version: str | None = None


class HealthOut(ApiModel):
    status: Literal["ok", "degraded"]
    database: bool
    provider_mode: str
    registry_hash: str
    registry_errors: list[str] | None
    ffmpeg: bool
    media_root: str
    local_engine: LocalEngineOut
    llm_mode: str = Field(description="mock or live (Claude prompt enhancement, scripts)")
    llm_key_set: bool
    reloads_on_change: bool = Field(False, description="The API restarts when its code changes (started with "
                                                       "--reload); a restart drops requests in flight")
    reveal_files: bool = Field(False, description="This browser runs on the API's machine, so files can be shown "
                                                  "in its file manager (POST /assets/{id}/reveal)")


# ---------------------------------------------------------------- presets


class PresetInputOut(ApiModel):
    role: str
    label: str
    slot: MediaSlot


class PresetMoveOut(ApiModel):
    id: str
    label: str
    category: str
    glyph: str = ""


class PresetOut(ApiModel):
    id: str
    name: str
    category: str
    summary: str
    tags: list[str]
    model_id: str
    model_name: str
    output_type: str
    mode: str
    mode_label: str
    aspect_ratio: str = Field(description="Shape of the result, for drawing the card")
    fields: list[PresetField]
    inputs: list[PresetInputOut]
    moves: list[PresetMoveOut]
    look: list[str] = Field(description="Labels of the cinema-look choices the preset applies")
    local: bool = Field(description="Runs free on the local GPU")
    live_price_usd: float | None = Field(None, description="Cheapest paid route with the preset's settings")


class PresetRenderIn(ApiModel):
    fields: dict[str, str] = {}
    media: list[MediaInput] = []


class PresetRenderOut(ApiModel):
    """An ordinary generation request: post it to /generations, or load it into Create."""

    model_id: str
    mode: str
    prompt: str
    params: dict[str, Any]
    media: list[MediaInput]
    direction: ShotDirection | None = None


class TemplateOut(ApiModel):
    """A guided template: a recipe run that asks only for these answers and inputs."""

    id: str
    name: str
    summary: str
    tags: list[str]
    recipe: str
    recipe_name: str
    makes: Literal["video", "image"] = Field("video", description="What it makes: which shelf it sits on")
    fields: list[PresetField]
    inputs: list[TemplateInput]


class TemplateRenderIn(ApiModel):
    fields: dict[str, str] = {}
    media: dict[str, str] = Field({}, description="Input key -> asset id, or a saved character's handle")


# ---------------------------------------------------------------- Claude


class EnhanceIn(ApiModel):
    model_id: str
    mode: str
    prompt: str = Field(min_length=1, max_length=4000)
    direction: ShotDirection | None = None


class EnhanceOut(ApiModel):
    prompt: str
    changes: list[str]
    warnings: list[str] = []
    model: str
    mock: bool = Field(description="LLM_MODE=mock: a canned rewrite, nothing was billed")
    cost_usd: float


class ClaudeCallOut(ApiModel):
    model: str = Field(description="e.g. claude-sonnet-5-5")
    name: str = Field(description="e.g. Claude Sonnet 5.5")
    usd: float = Field(description="A typical call at live rates")


class ClaudeCallsOut(ApiModel):
    mode: Literal["mock", "live"] = Field(description="Claude's mode in effect: mock calls are free")
    verified: str = Field(description="When the token prices were checked")
    calls: dict[str, ClaudeCallOut] = Field(description="By the spend ledger's purpose (enhance, cinema.plan, ...)")


# ---------------------------------------------------------------- pipelines


class RecipeOut(ApiModel):
    id: str
    name: str
    summary: str
    input_schema: dict[str, Any]


class RunCreate(ApiModel):
    recipe: str
    input: dict[str, Any]
    title: str | None = Field(None, max_length=200)
    cap_usd: float | None = Field(None, ge=0, description="Most the run may spend; defaults to your run cap")
    confirm_cost_usd: float | None = Field(None, description="Echo the estimate to confirm an expensive run")


class RunEstimateOut(ApiModel):
    estimate_usd: float = Field(description="On the routes that would run now (mock routes are free)")
    live_estimate_usd: float | None = Field(None, description="On the cheapest paid routes")
    generations: int
    models: list[str] = Field([], description="Names of the models it makes them with, in the order they're used")
    cap_usd: float
    by_key: dict[str, float] = Field({}, description="estimate_usd split by what each job is for (a Cinema shot's "
                                                     "id), where the recipe says")
    live_by_key: dict[str, float] | None = Field(None, description="live_estimate_usd split the same way")


class StepOut(ApiModel):
    key: str
    kind: str
    label: str
    status: str
    depends_on: list[str]
    error: str | None
    output: dict[str, Any]
    options: list[dict[str, Any]] = Field(default_factory=list, description="Checkpoint choices")
    generation_id: uuid.UUID | None
    generation_status: str | None = None
    progress: float | None = None
    assets: list[AssetOut] = []
    started_at: datetime | None
    finished_at: datetime | None


class RunSummary(ApiModel):
    id: uuid.UUID
    recipe: str
    recipe_name: str
    title: str
    status: str
    error: str | None
    estimate_usd: float | None
    cap_usd: float | None
    created_at: datetime
    finished_at: datetime | None
    output_asset: AssetOut | None = None
    steps_done: int = 0
    steps_total: int = 0


class RunOut(RunSummary):
    input: dict[str, Any]
    output: dict[str, Any]
    spent_usd: float
    steps: list[StepOut]


class RunRevise(ApiModel):
    input: dict[str, Any]


class CheckpointAnswer(ApiModel):
    choice: str
