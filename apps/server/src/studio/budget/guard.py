"""Spend guard and ledger.

- API: `needs_confirmation()` makes expensive requests come back with an explicit confirmation.
- Worker: `reserve()` locks the user's budget row, checks caps against charges + open reservations,
  then records a reservation. `settle()` releases it and records the actual charge.
$0 (mock) work never touches the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import BudgetPolicy, GenerationAttempt, SpendLedger
from studio.domain.enums import LedgerEntry

ZERO = Decimal("0")


@dataclass
class SpendSummary:
    today_usd: Decimal
    month_usd: Decimal
    reserved_usd: Decimal
    daily_cap_usd: Decimal
    monthly_cap_usd: Decimal
    confirm_over_usd: Decimal
    live_enabled: bool


async def get_policy(session: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> BudgetPolicy:
    stmt = select(BudgetPolicy).where(BudgetPolicy.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    policy = (await session.execute(stmt)).scalar_one_or_none()
    if policy is None:
        policy = BudgetPolicy(user_id=user_id)
        session.add(policy)
        await session.flush()
    return policy


def _windows(now: datetime) -> tuple[datetime, datetime]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


async def summary(session: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> SpendSummary:
    policy = await get_policy(session, user_id, lock=lock)
    day, month = _windows(datetime.now(UTC))
    row = (await session.execute(text("""
        SELECT
          COALESCE(SUM(amount_usd) FILTER (WHERE entry_type = 'charge' AND created_at >= :day), 0)   AS today,
          COALESCE(SUM(amount_usd) FILTER (WHERE entry_type = 'charge' AND created_at >= :month), 0) AS month,
          COALESCE((
            SELECT SUM(r.amount_usd) FROM spend_ledger r
            WHERE r.user_id = :u AND r.entry_type = 'reservation'
              AND NOT EXISTS (SELECT 1 FROM spend_ledger x
                              WHERE x.generation_attempt_id = r.generation_attempt_id
                                AND x.entry_type = 'release')
          ), 0) AS reserved
        FROM spend_ledger WHERE user_id = :u
    """), {"u": user_id, "day": day, "month": month})).one()
    return SpendSummary(
        today_usd=Decimal(row.today), month_usd=Decimal(row.month), reserved_usd=Decimal(row.reserved),
        daily_cap_usd=policy.daily_cap_usd, monthly_cap_usd=policy.monthly_cap_usd,
        confirm_over_usd=policy.confirm_over_usd, live_enabled=policy.live_enabled,
    )


def needs_confirmation(estimate_usd: Decimal, confirm_over_usd: Decimal, confirmed_usd: float | None) -> bool:
    if estimate_usd <= confirm_over_usd:
        return False
    # The confirmation must cover the current estimate (prices or params may have changed since).
    return confirmed_usd is None or Decimal(str(confirmed_usd)) < estimate_usd


class BudgetBlocked(Exception):
    pass


async def reserve(session: AsyncSession, user_id: uuid.UUID, attempt: GenerationAttempt, amount: Decimal) -> None:
    """Reserve spend for an attempt or raise BudgetBlocked. Caller commits."""
    if amount <= ZERO:
        return
    s = await summary(session, user_id, lock=True)  # FOR UPDATE on the policy row serializes reservations
    if not s.live_enabled:
        raise BudgetBlocked("live generation is switched off in settings")
    exposure_today = s.today_usd + s.reserved_usd + amount
    exposure_month = s.month_usd + s.reserved_usd + amount
    if exposure_today > s.daily_cap_usd:
        raise BudgetBlocked(f"daily cap ${s.daily_cap_usd:.2f} would be exceeded (${exposure_today:.2f})")
    if exposure_month > s.monthly_cap_usd:
        raise BudgetBlocked(f"monthly cap ${s.monthly_cap_usd:.2f} would be exceeded (${exposure_month:.2f})")
    session.add(SpendLedger(
        user_id=user_id, entry_type=LedgerEntry.reservation, amount_usd=amount, provider=attempt.provider,
        route_id=attempt.route_id, generation_attempt_id=attempt.id, estimated=True,
    ))


async def settle(
    session: AsyncSession, user_id: uuid.UUID, attempt: GenerationAttempt, charge: Decimal | None,
    *, estimated: bool, basis: dict | None = None,
) -> None:
    """Release the reservation (if any) and record the charge (if any). Idempotent per attempt."""
    existing = {
        e for (e,) in (await session.execute(
            select(SpendLedger.entry_type).where(SpendLedger.generation_attempt_id == attempt.id)
        )).all()
    }
    reserved = (await session.execute(
        select(SpendLedger.amount_usd).where(
            SpendLedger.generation_attempt_id == attempt.id, SpendLedger.entry_type == LedgerEntry.reservation
        )
    )).scalar_one_or_none()
    if reserved is not None and LedgerEntry.release not in existing:
        session.add(SpendLedger(
            user_id=user_id, entry_type=LedgerEntry.release, amount_usd=-reserved, provider=attempt.provider,
            route_id=attempt.route_id, generation_attempt_id=attempt.id,
        ))
    if charge is not None and charge > ZERO and LedgerEntry.charge not in existing:
        session.add(SpendLedger(
            user_id=user_id, entry_type=LedgerEntry.charge, amount_usd=charge, provider=attempt.provider,
            route_id=attempt.route_id, generation_attempt_id=attempt.id, estimated=estimated, basis=basis or {},
        ))
