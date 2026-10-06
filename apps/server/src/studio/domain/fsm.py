"""Generation state machine and event emission.

`transition()` is the ONLY way a generation's status changes. In one transaction it updates the row,
appends a `job_events` row (for SSE replay) and calls pg_notify (delivered on commit), so the browser
never sees a status the database doesn't have.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Generation, JobEvent
from studio.domain.enums import ErrorClass, GenStatus
from studio.pipelines.hooks import mark_run

CHANNEL = "studio_events"
NOTIFY_LIMIT = 7000  # Postgres caps NOTIFY payloads at 8000 bytes

S = GenStatus
ALLOWED: dict[GenStatus, frozenset[GenStatus]] = {
    S.draft: frozenset({S.queued, S.awaiting_confirmation, S.blocked_budget, S.canceled}),
    S.awaiting_confirmation: frozenset({S.queued, S.canceled}),
    S.blocked_budget: frozenset({S.queued, S.canceled}),
    # queued -> failed covers "no eligible route"; -> blocked_budget is the worker-side cap check
    S.queued: frozenset({S.submitting, S.blocked_budget, S.failed, S.canceled}),
    # submitting -> queued: wait for a provider slot or fail over before the provider accepted the job
    S.submitting: frozenset({S.in_progress, S.queued, S.failed, S.unknown, S.canceled}),
    # in_progress -> queued: post-acceptance failover (new attempt)
    S.in_progress: frozenset({S.ingesting, S.queued, S.failed, S.canceled}),
    S.ingesting: frozenset({S.succeeded, S.failed}),
    # unknown is resolved by a human: mark failed, or attach the provider job id and keep polling
    S.unknown: frozenset({S.in_progress, S.failed, S.canceled}),
    S.succeeded: frozenset(),
    S.failed: frozenset(),
    S.canceled: frozenset(),
}


class IllegalTransition(Exception):
    pass


def can_transition(current: GenStatus, new: GenStatus) -> bool:
    return new in ALLOWED[current]


def generation_event_payload(gen: Generation) -> dict[str, Any]:
    return {
        "generation_id": str(gen.id),
        "status": gen.status.value,
        "status_reason": gen.status_reason,
        "progress": gen.progress,
        "model_id": gen.model_id,
        "mode": gen.mode,
        "estimated_cost_usd": float(gen.estimated_cost_usd) if gen.estimated_cost_usd is not None else None,
        "actual_cost_usd": float(gen.actual_cost_usd) if gen.actual_cost_usd is not None else None,
        "route_id": (gen.routing or {}).get("chosen"),
        "error_code": gen.error_code.value if gen.error_code else None,
        "error_message": (gen.error_message or "")[:300] or None,
    }


async def notify(session: AsyncSession, message: dict[str, Any]) -> None:
    body = json.dumps(message, default=str)
    if len(body) > NOTIFY_LIMIT:
        body = json.dumps({**{k: message[k] for k in ("id", "topic", "type", "subject_id", "user_id")
                              if k in message}, "truncated": True})
    await session.execute(text("SELECT pg_notify(:ch, :body)"), {"ch": CHANNEL, "body": body})


async def emit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    topic: str,
    type: str,
    subject_id: uuid.UUID | None,
    payload: dict[str, Any],
    persist: bool = True,
) -> int | None:
    """Record (optionally) and broadcast an event. Non-persisted events (progress ticks) have no id."""
    event_id: int | None = None
    if persist:
        ev = JobEvent(user_id=user_id, topic=topic, type=type, subject_id=subject_id, payload=payload)
        session.add(ev)
        await session.flush()
        event_id = ev.id
    await notify(session, {
        "id": event_id,
        "user_id": str(user_id),
        "topic": topic,
        "type": type,
        "subject_id": str(subject_id) if subject_id else None,
        "payload": payload,
    })
    return event_id


async def transition(
    session: AsyncSession,
    gen: Generation,
    new: GenStatus,
    *,
    reason: str | None = None,
    error: tuple[ErrorClass, str] | None = None,
    **fields: Any,
) -> None:
    current = gen.status
    if new == current and not fields and reason == gen.status_reason:
        return
    if new != current and not can_transition(current, new):
        raise IllegalTransition(f"generation {gen.id}: {current} -> {new} is not allowed")

    now = datetime.now(UTC)
    gen.status = new
    gen.status_reason = reason
    if error:
        gen.error_code, gen.error_message = error[0], error[1][:4000]
    for key, value in fields.items():
        setattr(gen, key, value)
    if new == S.queued and gen.queued_at is None:
        gen.queued_at = now
    if new in (S.submitting, S.in_progress) and gen.started_at is None:
        gen.started_at = now
    if new == S.succeeded:
        gen.progress = 1.0
    if new.terminal:
        gen.finished_at = now
        if gen.pipeline_step_id and gen.group_id:
            mark_run(session.sync_session, gen.group_id)  # its run advances once this commits

    await session.flush()
    await emit(
        session,
        user_id=gen.user_id,
        topic="generation",
        type=f"generation.{new.value}",
        subject_id=gen.id,
        payload=generation_event_payload(gen),
    )


async def report_progress(session: AsyncSession, gen: Generation, progress: float, *, estimated: bool) -> None:
    """Progress ticks are broadcast but not persisted as events (replay only needs the latest state)."""
    gen.progress = progress
    gen.progress_is_estimated = estimated
    await session.flush()
    await emit(
        session,
        user_id=gen.user_id,
        topic="generation",
        type="generation.progress",
        subject_id=gen.id,
        payload=generation_event_payload(gen),
        persist=False,
    )
