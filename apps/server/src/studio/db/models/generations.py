from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, Money, TimestampMixin
from studio.db.models._types import str_enum
from studio.domain.enums import AttemptStatus, ErrorClass, GenKind, GenStatus

_ACTIVE_SQL = "status NOT IN ('succeeded', 'failed', 'canceled', 'draft')"


class Generation(IdMixin, TimestampMixin, Base):
    """One model call, run as a durable job. Attempts hold the per-route provider work."""

    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_generations_active", "status", postgresql_where=text(_ACTIVE_SQL)),
        Index("ix_generations_user_created", "user_id", "created_at"),
        Index("ix_generations_project_shot", "project_id", "shot_key"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[uuid.UUID | None]
    parent_generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generations.id"))
    kind: Mapped[GenKind] = mapped_column(str_enum(GenKind), default=GenKind.generate)

    model_id: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(40))
    output_type: Mapped[str] = mapped_column(String(16))

    status: Mapped[GenStatus] = mapped_column(str_enum(GenStatus), default=GenStatus.queued)
    status_reason: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[float] = mapped_column(default=0.0)
    progress_is_estimated: Mapped[bool] = mapped_column(default=True)

    # Canonical request as posted: {prompt, params, media: [{role, asset_id}], route_policy}
    request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Director output (final prompt, attached refs, native params, warnings). Filled at submit time.
    compiled: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Route decision: {policy, candidates: [...], chosen, failovers: [...]}
    routing: Mapped[dict[str, Any]] = mapped_column(default=dict)

    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Money)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Money)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    project_id: Mapped[uuid.UUID | None]
    shot_key: Mapped[str | None] = mapped_column(String(80))
    pipeline_step_id: Mapped[uuid.UUID | None]
    preset_id: Mapped[uuid.UUID | None]
    registry_hash: Mapped[str | None] = mapped_column(String(64))

    error_code: Mapped[ErrorClass | None] = mapped_column(str_enum(ErrorClass))
    error_message: Mapped[str | None] = mapped_column(Text)

    queued_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class GenerationAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("route_id", "provider_job_id"),
        UniqueConstraint("generation_id", "attempt_no"),
        Index(
            "ix_attempts_inflight_provider",
            "provider",
            postgresql_where=text("status IN ('submitting', 'submitted', 'running')"),
        ),
    )

    generation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generations.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    route_id: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40))
    provider_job_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[AttemptStatus] = mapped_column(str_enum(AttemptStatus), default=AttemptStatus.submitting)

    # Exact provider request (secrets scrubbed, local paths instead of signed URLs)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_response: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error_class: Mapped[ErrorClass | None] = mapped_column(str_enum(ErrorClass))
    error_message: Mapped[str | None] = mapped_column(Text)

    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Money)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Money)
    cost_basis: Mapped[dict[str, Any]] = mapped_column(default=dict)

    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    next_poll_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class JobEvent(Base):
    """Append-only event log. Backs SSE replay via Last-Event-ID; pruned after 7 days."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    topic: Mapped[str] = mapped_column(String(40))  # generation | asset | system
    subject_id: Mapped[uuid.UUID | None]
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
