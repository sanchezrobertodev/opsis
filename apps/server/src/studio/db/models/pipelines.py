from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, Money, TimestampMixin
from studio.db.models._types import str_enum


class RunStatus(StrEnum):
    running = "running"
    waiting = "waiting"  # a checkpoint needs the user, or a step is blocked
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting = "waiting"  # checkpoint: waiting for the user's choice
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"
    skipped = "skipped"  # no longer part of the plan (e.g. a shot was removed)


class StepKind(StrEnum):
    generate = "generate"  # one Generation (model call), tracked by generation_id
    local = "local"  # ffmpeg and other local work, run by the pipeline.step task
    llm = "llm"  # a Claude call
    checkpoint = "checkpoint"  # the user approves or picks


class PipelineRun(IdMixin, TimestampMixin, Base):
    """One run of a recipe: its input, cap, status and final output."""

    __tablename__ = "pipeline_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    recipe: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), default=RunStatus.running, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cap_usd: Mapped[Decimal | None] = mapped_column(Money)
    estimate_usd: Mapped[Decimal | None] = mapped_column(Money)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None]


class PipelineStep(IdMixin, TimestampMixin, Base):
    """A node in a run. `key` is stable across re-plans (e.g. `shot[02]`) so edits only redo what changed."""

    __tablename__ = "pipeline_steps"
    __table_args__ = (UniqueConstraint("run_id", "key"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    kind: Mapped[StepKind] = mapped_column(str_enum(StepKind))
    label: Mapped[str] = mapped_column(String(200), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[StepStatus] = mapped_column(str_enum(StepStatus), default=StepStatus.pending)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    input_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generations.id"))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
