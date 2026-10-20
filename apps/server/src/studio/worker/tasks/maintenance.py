"""Reaper: makes the job pipeline self-healing after crashes or restarts. Runs every minute."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from studio.db.models import (
    Generation,
    GenerationAttempt,
    JobEvent,
    PipelineRun,
    PipelineStep,
    RunStatus,
    StepKind,
    StepStatus,
)
from studio.db.session import session_scope
from studio.domain import fsm
from studio.domain.enums import AttemptStatus, ErrorClass, GenStatus
from studio.storage.local import get_storage
from studio.worker.app import Q_MAINT, app
from studio.worker.tasks.generation import (
    PREPARE_TIMEOUT_S,
    SUBMIT_TIMEOUT_S,
    defer_ingest,
    defer_poll,
    defer_submit,
)
from studio.worker.tasks.pipeline import defer_advance

log = logging.getLogger(__name__)

STALE_QUEUED = timedelta(seconds=60)
STALE_POLL = timedelta(minutes=2)
# longer than a submit can legitimately take, so a slow upload is never mistaken for a crashed worker
STUCK_SUBMIT = timedelta(seconds=PREPARE_TIMEOUT_S + SUBMIT_TIMEOUT_S + 120)
STALE_INGEST = timedelta(minutes=5)
STALE_RUN = timedelta(minutes=2)
EVENT_RETENTION = timedelta(days=7)
ABANDONED_RENDER = timedelta(days=1)


@app.periodic(cron="* * * * *")
@app.task(name="maint.reap", queue=Q_MAINT, queueing_lock="maint.reap")
async def maint_reap(timestamp: int) -> None:
    await reap(datetime.now(UTC))


async def reap(now: datetime) -> dict[str, int]:
    counts = {"resubmitted": 0, "repolled": 0, "unknown": 0, "reingested": 0, "runs": 0}
    async with session_scope() as s:
        # API committed `queued` but died before deferring, or a deferred job was lost
        queued = (await s.execute(select(Generation.id).where(
            Generation.status == GenStatus.queued, Generation.updated_at < now - STALE_QUEUED))).scalars().all()
        # Polls that should have happened by now
        polls = (await s.execute(select(GenerationAttempt.id).where(
            GenerationAttempt.status.in_([AttemptStatus.submitted, AttemptStatus.running]),
            GenerationAttempt.next_poll_at < now - STALE_POLL))).scalars().all()
        ingesting = (await s.execute(select(Generation.id).where(
            Generation.status == GenStatus.ingesting, Generation.updated_at < now - STALE_INGEST))).scalars().all()
        ingest_attempts = (await s.execute(select(GenerationAttempt.id).where(
            GenerationAttempt.generation_id.in_(ingesting),
            GenerationAttempt.status == AttemptStatus.succeeded))).scalars().all() if ingesting else []

        # Submit crashed after creating the attempt but before recording a provider job id: we can't
        # know whether the provider accepted it, so a human decides (never auto-resubmit paid work).
        stuck = (await s.execute(select(GenerationAttempt).where(
            GenerationAttempt.status == AttemptStatus.submitting,
            GenerationAttempt.provider_job_id.is_(None),
            GenerationAttempt.created_at < now - STUCK_SUBMIT).with_for_update())).scalars().all()
        for attempt in stuck:
            gen = await s.get(Generation, attempt.generation_id, with_for_update=True)
            attempt.status = AttemptStatus.unknown
            if gen and gen.status == GenStatus.submitting:
                await fsm.transition(s, gen, GenStatus.unknown, reason="worker stopped mid-submit",
                                     error=(ErrorClass.transient, "submit outcome unknown after a worker restart"))
                counts["unknown"] += 1

        # a run whose wake-up was lost (process died between commit and defer)
        runs = (await s.execute(select(PipelineRun.id).where(
            PipelineRun.status == RunStatus.running, PipelineRun.updated_at < now - STALE_RUN))).scalars().all()
        # a run that ended (a step failed) while other takes rendered: settle the steps whose take has finished
        runs += (await s.execute(select(PipelineStep.run_id).distinct()
                                 .join(PipelineRun, PipelineRun.id == PipelineStep.run_id)
                                 .join(Generation, Generation.id == PipelineStep.generation_id)
                                 .where(PipelineRun.status.in_([RunStatus.failed, RunStatus.canceled]),
                                        PipelineStep.kind == StepKind.generate,
                                        PipelineStep.status == StepStatus.running,
                                        Generation.status.in_([GenStatus.succeeded, GenStatus.failed,
                                                               GenStatus.canceled])))).scalars().all()

        await s.execute(delete(JobEvent).where(JobEvent.created_at < now - EVENT_RETENTION))

    for gid in queued:
        await defer_submit(gid)
        counts["resubmitted"] += 1
    for aid in polls:
        await defer_poll(aid)
        counts["repolled"] += 1
    for aid in ingest_attempts:
        await defer_ingest(aid)
        counts["reingested"] += 1
    for rid in runs:
        await defer_advance(rid)
        counts["runs"] += 1
    counts["renders"] = _sweep_renders(now)
    if any(counts.values()):
        log.info("reaper: %s", counts)
    return counts


def _sweep_renders(now: datetime) -> int:
    """Frames of a 3D render that was never finished (the tab closed): `/finish` removes the rest."""
    folder = get_storage().temp_dir() / "renders"
    old = [d for d in folder.iterdir() if d.is_dir() and d.stat().st_mtime < (now - ABANDONED_RENDER).timestamp()] \
        if folder.is_dir() else []
    for d in old:
        shutil.rmtree(d, ignore_errors=True)
    return len(old)


@app.periodic(cron="23 * * * *")
@app.task(name="budget.reconcile", queue=Q_MAINT, queueing_lock="budget.reconcile")
async def budget_reconcile(timestamp: int) -> None:
    """Every hour: read fal's and our RunPod endpoints' bills (free) and book what the estimates missed."""
    from studio.budget.reconcile import reconcile

    async with session_scope() as s:
        results = await reconcile(s, days=3)
    for r in results:
        if r.booked_usd or r.status not in ("ok", "skipped"):
            log.info("reconcile %s: %s, %d checked, booked %+.4f%s", r.provider, r.status, r.checked, r.booked_usd,
                     f" ({r.detail})" if r.detail else "")
