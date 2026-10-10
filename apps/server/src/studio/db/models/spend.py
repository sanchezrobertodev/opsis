from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, Money
from studio.db.models._types import str_enum
from studio.domain.enums import BreakerState, ErrorClass, LedgerEntry


class BudgetPolicy(Base):
    __tablename__ = "budget_policies"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    live_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_cap_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("20"))
    monthly_cap_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("200"))
    confirm_over_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("2"))
    default_run_cap_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("10"))
    # The Settings switch: mock / mixed / live and mock / live, capped by PROVIDER_MODE / LLM_MODE in .env
    # (budget/modes.py). NULL means whatever .env says.
    provider_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    llm_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=text("now()"))


class SpendLedger(IdMixin, Base):
    """Append-only money log: reservations at submit, then a charge or release when settled."""

    __tablename__ = "spend_ledger"
    __table_args__ = (
        UniqueConstraint("generation_attempt_id", "entry_type"),
        # a bill correction no take paid for (an idle RunPod worker) is booked once per provider hour
        Index("uq_spend_ledger_idle_bucket", "provider", text("(basis->>'bucket')"), unique=True,
              postgresql_where=text("entry_type = 'adjustment' AND generation_attempt_id IS NULL")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    entry_type: Mapped[LedgerEntry] = mapped_column(str_enum(LedgerEntry))
    amount_usd: Mapped[Decimal] = mapped_column(Money)  # signed: reservations/charges positive, releases negative
    provider: Mapped[str | None] = mapped_column(String(40))
    route_id: Mapped[str | None] = mapped_column(String(120))
    generation_attempt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_attempts.id"))
    pipeline_run_id: Mapped[uuid.UUID | None]
    llm_call_id: Mapped[uuid.UUID | None]
    basis: Mapped[dict[str, Any]] = mapped_column(default=dict)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), index=True)


class ProviderRouteHealth(Base):
    __tablename__ = "provider_route_health"

    route_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    breaker_state: Mapped[BreakerState] = mapped_column(str_enum(BreakerState), default=BreakerState.closed)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    open_until: Mapped[datetime | None]
    success_24h: Mapped[int] = mapped_column(Integer, default=0)
    failure_24h: Mapped[int] = mapped_column(Integer, default=0)
    p50_latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    p95_latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    last_success_at: Mapped[datetime | None]
    last_failure_at: Mapped[datetime | None]
    last_error_class: Mapped[ErrorClass | None] = mapped_column(str_enum(ErrorClass))
    last_error: Mapped[str | None] = mapped_column(Text)
