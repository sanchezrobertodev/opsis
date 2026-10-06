"""API request/response models. These define the OpenAPI schema the web client is generated from."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.registry.schema import DurationSpec, Mode, Param, PromptSpec, Rule


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


class CandidateOut(ApiModel):
    route_id: str
    provider: str
    provider_name: str | None = None
    eligible: bool
    reason: str | None
    estimate_usd: float | None
    pricing_basis: str | None
    is_mock: bool


class EstimateOut(ApiModel):
    chosen_route_id: str | None
    estimate_usd: float | None
    live_reference: CandidateOut | None
    candidates: list[CandidateOut]
    warnings: list[str]
    params: dict[str, Any]


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
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    assets: list[AssetOut] = []


class GenerationPage(ApiModel):
    items: list[GenerationOut]
    next_cursor: str | None


class AssetPage(ApiModel):
    items: list[AssetOut]
    next_cursor: str | None


class AssetPatch(ApiModel):
    title: str | None = None
    tags: list[str] | None = None
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


class BudgetUpdate(ApiModel):
    daily_cap_usd: float | None = Field(None, ge=0)
    monthly_cap_usd: float | None = Field(None, ge=0)
    confirm_over_usd: float | None = Field(None, ge=0)
    live_enabled: bool | None = None


class HealthOut(ApiModel):
    status: Literal["ok", "degraded"]
    database: bool
    provider_mode: str
    registry_hash: str
    registry_errors: list[str] | None
    ffmpeg: bool
    media_root: str
