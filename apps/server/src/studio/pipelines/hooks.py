"""Wake a run when one of its generations finishes.

fsm.transition() notes the run in the session; after that transaction commits, `pipeline.advance` is
deferred for it. Deferring after the commit (not before) means advance always sees the finished take.
If the process dies in between, the reaper's stuck-run sweep catches it within a minute.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

INFO_KEY = "pipeline_runs_to_advance"
_tasks: set[asyncio.Task] = set()


def mark_run(session: Session, run_id: uuid.UUID) -> None:
    session.info.setdefault(INFO_KEY, set()).add(run_id)


async def _defer(run_id: uuid.UUID) -> None:
    from studio.worker.tasks.pipeline import defer_advance

    try:
        await defer_advance(run_id)
    except Exception:
        log.exception("could not defer pipeline.advance for run %s; the reaper will pick it up", run_id)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    runs = session.info.pop(INFO_KEY, None)
    if not runs:
        return
    loop = asyncio.get_running_loop()
    for run_id in runs:
        task = loop.create_task(_defer(run_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(INFO_KEY, None)
