"""Event hub: one Postgres LISTEN connection fanned out to every connected browser (filtered by user).

Workers publish via pg_notify inside their transactions (studio.domain.fsm), so events reach the
browser only after the change they describe is committed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

import asyncpg

from studio.config import get_settings
from studio.domain.fsm import CHANNEL

log = logging.getLogger(__name__)


class Subscriber:
    def __init__(self, user_id: uuid.UUID):
        self.user_id = str(user_id)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)


class EventHub:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-hub")

    async def _run(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
        while not self._stopping:
            try:
                self._conn = await asyncpg.connect(dsn)
                await self._conn.add_listener(CHANNEL, self._on_notify)
                log.info("event hub listening on %s", CHANNEL)
                while not self._stopping and not self._conn.is_closed():
                    await asyncio.sleep(5)
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("event hub connection lost (%s); retrying", exc)
            finally:
                if self._conn is not None and not self._conn.is_closed():
                    with contextlib.suppress(Exception):
                        await self._conn.close()
                self._conn = None
            if not self._stopping:
                await asyncio.sleep(2)

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            return
        self.publish(msg)

    def publish(self, msg: dict[str, Any]) -> None:
        """Deliver to matching subscribers. Messages without user_id (system events) go to everyone."""
        target = msg.get("user_id")
        for sub in list(self._subs):
            if target and target != sub.user_id:
                continue
            try:
                sub.queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("dropping event for a slow client")

    def subscribe(self, user_id: uuid.UUID) -> Subscriber:
        sub = Subscriber(user_id)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


hub = EventHub()
