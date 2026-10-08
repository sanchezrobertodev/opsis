"""Reaper: makes the job pipeline self-healing after crashes or restarts. Runs every minute."""

from __future__ import annotations

import logging
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
from studio.worker.app import Q_MAINT, app
from studio.worker.tasks.generation import defer_ingest, defer_poll, defer_submit
from studio.worker.tasks.pipeline import defer_advance

log = logging.getLogger(__name__)

STALE_QUEUED = timedelta(seconds=60)
STALE_POLL = timedelta(minutes=2)
STUCK_SUBMIT = timedelta(minutes=2)
STALE_INGEST = timedelta(minutes=5)
STALE_RUN = timedelta(minutes=2)
EVENT_RETENTION = timedelta(days=7)


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
    if any(counts.values()):
        log.info("reaper: %s", counts)
    return counts
