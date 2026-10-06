"""Pipeline engine: reconcile a run's stored steps with its recipe's plan, and start whatever is ready.

`advance(run_id)` is the only thing that moves a run forward. It runs as a procrastinate job (one at a time
per run) whenever something may have changed: a run starts, a step's generation finishes, a local step
completes, the user answers a checkpoint. It is idempotent, so extra calls are harmless, and the reaper
calls it for runs that look stuck.

Generate steps create ordinary Generations (same routing, spend guard and ledger as Create), capped by the
run's budget. Local and Claude steps run in the `pipeline.step` task so long ffmpeg work never holds the run
lock. Checkpoints wait for the user.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget import guard
from studio.db.models import (
    Asset,
    Generation,
    PipelineRun,
    PipelineStep,
    RunStatus,
    StepKind,
    StepStatus,
)
from studio.domain import fsm
from studio.domain.enums import GenStatus
from studio.domain.generations import (
    ConfirmationRequired,
    CreateRequest,
    GenerationRequestError,
    cancel_generation,
    create_generation,
)
from studio.pipelines.ops import LOCAL_OPS, StepFailed
from studio.pipelines.recipe import Settled, StepSpec
from studio.pipelines.recipes import get_recipe
from studio.registry.loader import get_registry

log = logging.getLogger(__name__)

RUN_DONE = {RunStatus.succeeded, RunStatus.failed, RunStatus.canceled}
STEP_DONE = {StepStatus.succeeded, StepStatus.failed, StepStatus.canceled, StepStatus.skipped}


@dataclass
class Effects:
    """Work to hand to the queue once the transaction commits."""

    submit: list[uuid.UUID] = field(default_factory=list)  # generations
    steps: list[uuid.UUID] = field(default_factory=list)  # local/llm steps
    cancel_remote: list[uuid.UUID] = field(default_factory=list)  # attempts
    derive: list[uuid.UUID] = field(default_factory=list)  # new assets needing thumbnails
    advance: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _optional(step: PipelineStep) -> bool:
    return bool(step.input.get("_optional"))


def settled_outputs(steps: dict[str, PipelineStep]) -> Settled:
    out: Settled = {}
    for key, st in steps.items():
        if st.status == StepStatus.succeeded:
            out[key] = st.output
        elif st.status == StepStatus.failed and _optional(st):
            out[key] = {"failed": st.error or "failed"}
    return out


async def emit_run(session: AsyncSession, run: PipelineRun) -> None:
    await fsm.emit(session, user_id=run.user_id, topic="pipeline", type=f"pipeline.{run.status.value}",
                   subject_id=run.id, payload={"run_id": str(run.id), "status": run.status.value})


async def run_spent(session: AsyncSession, run: PipelineRun) -> Decimal:
    """What the run's generations are expected to cost so far (actual where known)."""
    total = (await session.execute(
        select(func.coalesce(func.sum(func.coalesce(Generation.actual_cost_usd, Generation.estimated_cost_usd)), 0))
        .where(Generation.group_id == run.id, Generation.status != GenStatus.canceled)
    )).scalar_one()
    return Decimal(total)


async def _sync_generation(session: AsyncSession, step: PipelineStep) -> None:
    """Fold a finished generation into its step."""
    gen = await session.get(Generation, step.generation_id)
    if gen is None:
        step.status, step.error = StepStatus.failed, "its generation is missing"
    elif gen.status == GenStatus.succeeded:
        ids = (await session.execute(select(Asset.id).where(Asset.generation_id == gen.id, Asset.deleted_at.is_(None))
                                     .order_by(Asset.output_index))).scalars().all()
        if not ids:
            step.status, step.error = StepStatus.failed, "the take finished without any media"
        else:
            step.status = StepStatus.succeeded
            step.output = {"generation_id": str(gen.id), "asset_id": str(ids[0]), "asset_ids": [str(i) for i in ids]}
    elif gen.status in (GenStatus.failed, GenStatus.canceled):
        step.status = StepStatus.failed
        step.error = gen.error_message or f"the take was {gen.status.value}"
    else:
        return
    step.finished_at = _now()


async def _start_generate(session: AsyncSession, run: PipelineRun, step: PipelineStep, fx: Effects) -> None:
    policy = await guard.get_policy(session, run.user_id)
    cap = run.cap_usd if run.cap_usd is not None else policy.default_run_cap_usd
    remaining = max(Decimal("0"), cap - await run_spent(session, run))
    body = CreateRequest(
        model_id=step.input["model_id"], mode=step.input["mode"], prompt=step.input.get("prompt", ""),
        params=step.input.get("params") or {}, media=step.input.get("media") or [],
        direction=step.input.get("direction"), group_id=run.id, pipeline_step_id=step.id,
        # the run's cap is the confirmation: anything that fits in what's left may start
        confirm_cost_usd=float(remaining),
        idempotency_key=f"run:{run.id}:{step.key}:{step.input_hash[:16]}:{step.attempts}",
    )
    try:
        gen, created = await create_generation(session, run.user_id, body)
    except ConfirmationRequired as exc:
        step.status = StepStatus.failed
        step.error = (f"this take is estimated at ${exc.estimate_usd:.2f}, more than the ${remaining:.2f} left "
                      f"in the run's ${cap:.2f} cap")
        step.finished_at = _now()
        return
    except GenerationRequestError as exc:
        step.status, step.error, step.finished_at = StepStatus.failed, "; ".join(exc.errors)[:2000], _now()
        return
    step.status, step.generation_id, step.started_at = StepStatus.running, gen.id, _now()
    if created:
        fx.submit.append(gen.id)


def _apply_spec(step: PipelineStep, spec: StepSpec, sort: int) -> None:
    step.kind, step.label, step.sort = spec.kind, spec.label, sort
    step.depends_on = list(spec.depends_on)
    step.input = {**spec.input, **({"_optional": True} if spec.optional else {})}
    step.input_hash = spec.digest()


async def reconcile(session: AsyncSession, run: PipelineRun, fx: Effects) -> None:
    """Bring a locked run up to date and start ready steps. Caller commits and then applies `fx`."""
    if run.status in RUN_DONE:
        return
    recipe = get_recipe(run.recipe)
    if recipe is None:
        run.status, run.error, run.finished_at = RunStatus.failed, f"unknown recipe {run.recipe!r}", _now()
        await emit_run(session, run)
        return
    reg = get_registry()
    inp = recipe.Input.model_validate(run.input)
    rows = (await session.execute(select(PipelineStep).where(PipelineStep.run_id == run.id)
                                  .with_for_update())).scalars().all()
    steps = {st.key: st for st in rows}

    for st in steps.values():
        if st.kind == StepKind.generate and st.status == StepStatus.running and st.generation_id:
            await _sync_generation(session, st)

    specs = recipe.plan(inp, settled_outputs(steps), reg)
    wanted: set[str] = set()
    for i, spec in enumerate(specs):
        wanted.add(spec.key)
        st = steps.get(spec.key)
        if st is None:
            st = PipelineStep(run_id=run.id, key=spec.key, status=StepStatus.pending)
            _apply_spec(st, spec, i)
            session.add(st)
            steps[spec.key] = st
        elif st.input_hash != spec.digest() and st.status not in (StepStatus.running, StepStatus.waiting):
            # its inputs changed (an edited shot, an upstream redo): do it again
            _apply_spec(st, spec, i)
            st.status, st.output, st.error, st.generation_id = StepStatus.pending, {}, None, None
            st.started_at = st.finished_at = None
        else:
            st.sort, st.label = i, spec.label
    for key, st in steps.items():
        if key not in wanted and st.status in (StepStatus.pending, StepStatus.waiting):
            st.status, st.finished_at = StepStatus.skipped, _now()
    await session.flush()

    live = [st for st in steps.values() if st.key in wanted]
    failed = next((st for st in live if st.status == StepStatus.failed and not _optional(st)), None)
    if failed:
        run.status, run.error, run.finished_at = RunStatus.failed, f"{failed.label}: {failed.error}"[:2000], _now()
        await emit_run(session, run)
        return

    settled = settled_outputs(steps)
    for st in sorted(live, key=lambda s: s.sort):
        if st.status != StepStatus.pending:
            continue
        if not all(d in settled for d in st.depends_on):
            continue
        st.attempts += 1
        if st.kind == StepKind.generate:
            await _start_generate(session, run, st, fx)
        elif st.kind == StepKind.checkpoint:
            st.status, st.started_at = StepStatus.waiting, _now()
        else:
            st.status, st.started_at = StepStatus.running, _now()
            fx.steps.append(st.id)
    await session.flush()

    # a failure while starting (cap, validation) ends the run now rather than on the next advance
    if any(st.status == StepStatus.failed and not _optional(st) for st in live):
        fx.advance = True

    settled = settled_outputs(steps)
    result = recipe.result(inp, settled)
    previous = run.status
    if result is not None and all(st.status in STEP_DONE for st in live):
        run.status, run.output, run.finished_at = RunStatus.succeeded, result, _now()
    elif any(st.status == StepStatus.waiting for st in live) and not any(
            st.status == StepStatus.running for st in live):
        run.status = RunStatus.waiting
    else:
        run.status = RunStatus.running
    if run.status != previous or fx.submit or fx.steps:
        await emit_run(session, run)


async def execute_step(session: AsyncSession, step: PipelineStep, run: PipelineRun) -> list[uuid.UUID]:
    """Run one local/llm step in place. Returns assets that need derivatives. Caller commits."""
    op = LOCAL_OPS.get(step.input.get("op", ""))
    try:
        if step.kind != StepKind.local or op is None:
            raise StepFailed(f"no runner for {step.kind.value} step op {step.input.get('op')!r}")
        output = await op(session, run, step)
    except StepFailed as exc:
        step.status, step.error = StepStatus.failed, str(exc)
    except Exception as exc:  # ffmpeg and friends: record it on the step rather than retrying forever
        log.exception("pipeline step %s (%s) failed", step.key, step.id)
        step.status, step.error = StepStatus.failed, f"{type(exc).__name__}: {str(exc)[-500:]}"
    else:
        derive = [uuid.UUID(a) for a in output.pop("derive", [])]
        step.status, step.output = StepStatus.succeeded, output
        step.finished_at = _now()
        return derive
    step.finished_at = _now()
    return []


async def cancel_run(session: AsyncSession, run: PipelineRun, fx: Effects) -> None:
    steps = (await session.execute(select(PipelineStep).where(PipelineStep.run_id == run.id)
                                   .with_for_update())).scalars().all()
    for st in steps:
        if st.status in STEP_DONE:
            continue
        if st.kind == StepKind.generate and st.generation_id:
            gen = await session.get(Generation, st.generation_id, with_for_update=True)
            if gen and not gen.status.terminal:
                remote = await cancel_generation(session, gen)
                if remote:
                    fx.cancel_remote.append(remote.id)
        st.status, st.finished_at = StepStatus.canceled, _now()
    run.status, run.finished_at = RunStatus.canceled, _now()
    await emit_run(session, run)


async def retry_run(session: AsyncSession, run: PipelineRun) -> None:
    """Give failed steps another go (each attempt is a fresh generation; paid work is never resubmitted)."""
    steps = (await session.execute(select(PipelineStep).where(
        PipelineStep.run_id == run.id, PipelineStep.status.in_([StepStatus.failed, StepStatus.canceled]))
        .with_for_update())).scalars().all()
    for st in steps:
        st.status, st.error, st.output, st.generation_id = StepStatus.pending, None, {}, None
        st.started_at = st.finished_at = None
    run.status, run.error, run.finished_at = RunStatus.running, None, None
    await emit_run(session, run)


async def resolve_checkpoint(session: AsyncSession, run: PipelineRun, key: str, choice: str) -> PipelineStep:
    step = (await session.execute(select(PipelineStep).where(
        PipelineStep.run_id == run.id, PipelineStep.key == key).with_for_update())).scalar_one_or_none()
    if step is None or step.kind != StepKind.checkpoint:
        raise LookupError(f"no checkpoint {key!r} in this run")
    if step.status != StepStatus.waiting:
        raise ValueError("this checkpoint isn't waiting for an answer")
    values = [str(o.get("value")) for o in step.input.get("options", [])]
    if values and choice not in values:
        raise ValueError("pick one of the offered options")
    step.status, step.output, step.finished_at = StepStatus.succeeded, {"choice": choice}, _now()
    if step.input.get("type") == "pick_one":
        asset = await session.get(Asset, uuid.UUID(choice))
        if asset and asset.user_id == run.user_id:
            asset.favorite = True  # the pick is the keeper
    return step
