"""Route health rows under concurrent takes."""

from __future__ import annotations

import asyncio
import uuid

from studio.db.models import ProviderRouteHealth
from studio.db.session import session_scope
from studio.domain.enums import ErrorClass
from studio.domain.health import record_failure, record_success


async def test_takes_finishing_at_once_on_a_new_route_all_count(db):
    """The first takes on a route used to race to insert its row; the losers' whole transaction (settle, succeeded)
    rolled back."""
    route = f"race-{uuid.uuid4().hex[:8]}@fal"

    async def finish(i: int) -> None:
        async with session_scope() as s:
            if i % 3:
                await record_success(s, route, "fal", 100)
            else:
                await record_failure(s, route, "fal", ErrorClass.transient, "busy")

    await asyncio.gather(*(finish(i) for i in range(6)))
    async with session_scope() as s:
        row = await s.get(ProviderRouteHealth, route)
        assert row is not None and (row.success_24h, row.failure_24h) == (4, 2)
