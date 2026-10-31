"""Per-route health and circuit breaker (3 consecutive failures -> open for 10 minutes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import ProviderRouteHealth
from studio.domain.enums import BreakerState, ErrorClass
from studio.registry.routing import RouteHealth

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = timedelta(minutes=10)
# Errors that say something about the route, not about this particular request
ROUTE_FAULTS = {ErrorClass.transient, ErrorClass.route_unavailable, ErrorClass.timeout}


async def _row(session: AsyncSession, route_id: str, provider: str) -> ProviderRouteHealth:
    # FOR UPDATE locks nothing until the row exists, so make it first: two takes finishing at once on a new route
    # would both insert it, and the loser's whole transaction (settle, succeeded) would roll back
    await session.execute(insert(ProviderRouteHealth).values(
        route_id=route_id, provider=provider, consecutive_failures=0, success_24h=0, failure_24h=0,
        breaker_state=BreakerState.closed).on_conflict_do_nothing(index_elements=["route_id"]))
    row = await session.get(ProviderRouteHealth, route_id, with_for_update=True, populate_existing=True)
    assert row is not None
    return row


async def record_success(session: AsyncSession, route_id: str, provider: str, latency_ms: int | None) -> None:
    row = await _row(session, route_id, provider)
    row.success_24h = (row.success_24h or 0) + 1
    row.consecutive_failures = 0
    row.breaker_state = BreakerState.closed
    row.open_until = None
    row.last_success_at = datetime.now(UTC)
    if latency_ms is not None:
        # cheap running estimate; P1 replaces this with real percentiles over attempts
        row.p50_latency_ms = latency_ms if row.p50_latency_ms is None else (row.p50_latency_ms * 3 + latency_ms) // 4


async def record_failure(session: AsyncSession, route_id: str, provider: str, error: ErrorClass, message: str) -> None:
    row = await _row(session, route_id, provider)
    row.failure_24h = (row.failure_24h or 0) + 1
    row.last_failure_at = datetime.now(UTC)
    row.last_error_class = error
    row.last_error = message[:2000]
    if error in ROUTE_FAULTS:
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        if row.consecutive_failures >= BREAKER_THRESHOLD:
            row.breaker_state = BreakerState.open
            row.open_until = datetime.now(UTC) + BREAKER_COOLDOWN


async def load_health(session: AsyncSession) -> dict[str, RouteHealth]:
    rows = (await session.execute(select(ProviderRouteHealth))).scalars().all()
    return {
        r.route_id: RouteHealth(
            breaker_open_until=r.open_until if r.breaker_state == BreakerState.open else None,
            success_24h=r.success_24h or 0,
            failure_24h=r.failure_24h or 0,
            p50_latency_ms=r.p50_latency_ms,
        )
        for r in rows
    }
