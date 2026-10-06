"""Pipeline jobs: `pipeline.advance` (reconcile a run) and `pipeline.step` (run one local step)."""

from __future__ import annotations

import logging
import uuid

from procrastinate.exceptions import AlreadyEnqueued

from studio.db.models import PipelineRun, PipelineStep, StepStatus
from studio.db.session import session_scope
from studio.pipelines.engine import Effects, execute_step, reconcile
from studio.worker.app import Q_CPU, Q_MAINT, app
from studio.worker.tasks.assets import defer_derive
from studio.worker.tasks.generation import defer_cancel_remote, defer_submit

log = logging.getLogger(__name__)


async def defer_advance(run_id: uuid.UUID) -> None:
    try:
        await pipeline_advance.configure(queueing_lock=f"advance:{run_id}", lock=f"run:{run_id}").defer_async(
            run_id=str(run_id))
    except AlreadyEnqueued:
        pass  # one queued advance per run is enough; it reads the latest state when it runs


async def defer_step(step_id: uuid.UUID) -> None:
    try:
        await pipeline_step.configure(queueing_lock=f"step:{step_id}").defer_async(step_id=str(step_id))
    except AlreadyEnqueued:
        pass


async def apply(fx: Effects, run_id: uuid.UUID) -> None:
    """Hand post-commit work to the queue."""
    for gen_id in fx.submit:
        await defer_submit(gen_id)
    for step_id in fx.steps:
        await defer_step(step_id)
    for attempt_id in fx.cancel_remote:
        await defer_cancel_remote(attempt_id)
    for asset_id in fx.derive:
        await defer_derive(asset_id)
    if fx.advance:
        await defer_advance(run_id)


@app.task(name="pipeline.advance", queue=Q_MAINT, retry=3)
async def pipeline_advance(run_id: str) -> None:
    await advance(uuid.UUID(run_id))


async def advance(run_id: uuid.UUID) -> None:
    fx = Effects()
    async with session_scope() as s:
        run = await s.get(PipelineRun, run_id, with_for_update=True)
        if run is None:
            return
        await reconcile(s, run, fx)
    await apply(fx, run_id)


@app.task(name="pipeline.step", queue=Q_CPU, retry=0)
async def pipeline_step(step_id: str) -> None:
    await run_step(uuid.UUID(step_id))


async def run_step(step_id: uuid.UUID) -> None:
    async with session_scope() as s:
        step = await s.get(PipelineStep, step_id, with_for_update=True)
        if step is None or step.status != StepStatus.running:
            return
        run = await s.get(PipelineRun, step.run_id)
        assert run is not None
        derive = await execute_step(s, step, run)
        run_id = run.id
    for asset_id in derive:
        await defer_derive(asset_id)
    await defer_advance(run_id)
