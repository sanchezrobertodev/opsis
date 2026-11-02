"""The reaper and the queue recover from a worker that died mid-job, and old provider leftovers are swept."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from studio.db.session import session_scope
from studio.providers.common import results_root
from studio.worker.app import app
from studio.worker.tasks.maintenance import reap
from studio.worker.tasks.pipeline import defer_advance


async def test_an_advance_left_doing_by_a_killed_worker_does_not_hold_up_its_run(in_memory_queue):
    run_id = uuid.uuid4()
    await defer_advance(run_id)
    (stuck,) = in_memory_queue.jobs.values()
    stuck["status"] = "doing"  # fetched, then the worker was killed: it stays `doing` forever
    await defer_advance(run_id)  # the reaper wakes the run again
    worker_id = await app.job_manager.register_worker()
    job = await app.job_manager.fetch_job(queues=None, worker_id=worker_id)
    assert job is not None and job.task_name == "pipeline.advance" and job.id != stuck["id"]


async def test_an_advance_never_waits_for_its_run_in_a_worker_slot(db, in_memory_queue):
    """A run held for minutes (a cancel waiting for an ffmpeg step) would otherwise fill every slot with its advances."""
    from studio.config import get_settings
    from studio.db.models import PipelineRun
    from studio.worker.tasks.pipeline import advance

    async with session_scope() as s:
        run = PipelineRun(user_id=get_settings().owner_user_id, recipe="sequence", title="held", input={})
        s.add(run)
    async with session_scope() as s:
        await s.get(PipelineRun, run.id, with_for_update=True)  # held while the advance runs
        start = time.monotonic()
        await advance(run.id)
        assert time.monotonic() - start < 5
    (later,) = in_memory_queue.jobs.values()
    assert later["task_name"] == "pipeline.advance" and later["args"] == {"run_id": str(run.id)}
    assert later["scheduled_at"] is not None  # comes back shortly


async def test_reap_frees_an_advance_queued_with_the_old_run_lock_behind_a_killed_worker(db, in_memory_queue):
    queue, run_id = f"test-{uuid.uuid4().hex}", uuid.uuid4()
    async with session_scope() as s:  # deferred by the old code: the killed worker's job, then the reaper's
        await s.execute(
            text(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, lock, queueing_lock, status) VALUES "
                "(:q, 'pipeline.advance', :lock, NULL, 'doing'), (:q, 'pipeline.advance', :lock, :ql, 'todo')"
            ),
            {"q": queue, "lock": f"run:{run_id}", "ql": f"advance:{run_id}"},
        )
        behind = (
            await s.execute(
                text("SELECT id FROM procrastinate_jobs WHERE queue_name = :q AND status = 'todo'"),
                {"q": queue},
            )
        ).scalar_one()

    await reap(datetime.now(UTC))
    async with session_scope() as s:
        worker = (
            await s.execute(text("SELECT worker_id FROM procrastinate_register_worker_v1()"))
        ).scalar_one()
        fetched = (
            await s.execute(
                text("SELECT id FROM procrastinate_fetch_job_v2(ARRAY[:q]::varchar[], :w)"),
                {"q": queue, "w": worker},
            )
        ).scalar_one()
    assert fetched == behind


async def test_parked_provider_results_are_swept_after_two_days(db, in_memory_queue):
    old, fresh = (results_root() / provider / uuid.uuid4().hex for provider in ("google", "elevenlabs"))
    for d in (old, fresh):
        d.mkdir(parents=True)
        (d / "out.png").write_bytes(b"png")
    three_days_ago = time.time() - 3 * 86400
    os.utime(old, (three_days_ago, three_days_ago))

    counts = await reap(datetime.now(UTC))
    assert counts["parked_results"] == 1
    assert not old.exists() and (fresh / "out.png").exists()
