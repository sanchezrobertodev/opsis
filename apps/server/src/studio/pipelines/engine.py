"""Pipeline engine: reconcile a run's stored steps with its recipe's plan, and start whatever is ready.

`advance(run_id)` is the only thing that moves a run forward. It runs as a procrastinate job (one at a time
per run) whenever something may have changed: a run starts, a step's generation finishes, a local step
completes, the user answers a checkpoint. It is idempotent, so extra calls are harmless, and the reaper
calls it for runs that look stuck.

Generate steps create ordinary Generations (same routing, spend guard and ledger as Create), capped by the
run's budget. Local and Claude steps run in the `pipeline.step` task so long ffmpeg work never holds the run
lock. Checkpoints wait for the user.

A take the provider refused on content grounds (moderation) gets one more try: Claude rewords the prompt in the
step task, keeping its meaning and @mentions, and the step starts again with it. If that try is refused too, or
Claude keeps the prompt as it was, the step fails as it would have (an optional one falls back as before).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget import guard
from studio.db.models import (
    Asset,
    Generation,
    GenerationAttempt,
    PipelineRun,
    PipelineStep,
    RunStatus,
    SpendLedger,
    StepKind,
    StepStatus,
)
from studio.domain import fsm
from studio.domain.enums import ACTIVE_STATUSES, SPENT, ErrorClass, GenStatus
from studio.domain.generations import (
    ConfirmationRequired,
    CreateRequest,
    GenerationRequestError,
    cancel_generation,
    create_generation,
)
from studio.llm.client import CURRENT_RUN
from studio.pipelines.llm_ops import LLM_OPS, reword
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
    """What the run's generations have cost so far: every charge the ledger holds for their attempts (a provider can
    bill an attempt that then failed), plus the estimate of each one still going (`unknown` too, since it may yet be
    billed). A finished generation nobody charged for costs nothing, so a rejected request doesn't use up the cap."""
    charged = (await session.execute(
        select(func.coalesce(func.sum(SpendLedger.amount_usd), 0))
        .join(GenerationAttempt, SpendLedger.generation_attempt_id == GenerationAttempt.id)
        .join(Generation, GenerationAttempt.generation_id == Generation.id)
        .where(Generation.group_id == run.id, SpendLedger.entry_type.in_(SPENT))
    )).scalar_one()
    pending = (await session.execute(
        select(func.coalesce(func.sum(Generation.estimated_cost_usd), 0))
        .where(Generation.group_id == run.id, Generation.status.in_(ACTIVE_STATUSES))
    )).scalar_one()
    claude = (await session.execute(  # its Claude steps (scripts, plans, rewording)
        select(func.coalesce(func.sum(SpendLedger.amount_usd), 0))
        .where(SpendLedger.pipeline_run_id == run.id, SpendLedger.generation_attempt_id.is_(None),
               SpendLedger.entry_type.in_(SPENT))
    )).scalar_one()
    return Decimal(charged) + Decimal(pending) + Decimal(claude)


def _texts(step: PipelineStep) -> list[tuple[str, int | None, str]]:
    """What a take was asked to show, as (where, shot index, text): its prompt, and each shot's prompt of a
    multi-shot take (whose own prompt is empty)."""
    out: list[tuple[str, int | None, str]] = []
    if str(step.input.get("prompt") or "").strip():
        out.append(("prompt", None, step.input["prompt"]))
    spec = get_registry().model(str(step.input.get("model_id") or ""))
    params = step.input.get("params") or {}
    for name in [p.name for p in spec.parameters if p.type == "shots"] if spec else []:
        for i, shot in enumerate(params.get(name) or []):
            if isinstance(shot, dict) and str(shot.get("prompt") or "").strip():
                out.append((name, i, shot["prompt"]))
    return out


def _with_texts(inp: dict, texts: list[tuple[str, int | None, str]], new: list[str]) -> dict:
    out = {**inp, "params": {**(inp.get("params") or {})}}
    for (where, i, _), text in zip(texts, new, strict=True):
        if i is None:
            out["prompt"] = text
        else:
            shots = out["params"][where] = list(out["params"][where])
            shots[i] = {**shots[i], "prompt": text}
    return out


def _rewordable(step: PipelineStep, gen: Generation) -> bool:
    """A take a provider refused on content grounds gets one more try with gentler prompts."""
    return (gen.status == GenStatus.failed and gen.error_code == ErrorClass.moderation and bool(_texts(step))
            and "_reword" not in step.input and "_reworded" not in step.input)


async def _sync_generation(session: AsyncSession, step: PipelineStep, *, may_reword: bool = False) -> bool:
    """Fold a finished generation into its step. True when, instead, the step should reword its refused prompt
    (it stays running; the caller runs it like a local step)."""
    gen = await session.get(Generation, step.generation_id)
    if gen is None:
        step.status, step.error = StepStatus.failed, "its generation is missing"
    elif gen.status == GenStatus.succeeded:
        rows = (await session.execute(select(Asset.id, Asset.duration_ms).where(
            Asset.generation_id == gen.id, Asset.deleted_at.is_(None)).order_by(Asset.output_index))).all()
        if not rows:
            step.status, step.error = StepStatus.failed, "the take finished without any media"
        else:
            step.status = StepStatus.succeeded
            # duration lets a recipe size what comes next (a clip as long as its narration)
            step.output = {"generation_id": str(gen.id), "asset_id": str(rows[0].id),
                           "asset_ids": [str(r.id) for r in rows], "duration_ms": rows[0].duration_ms}
    elif may_reword and _rewordable(step, gen):
        step.input = {**step.input, "_reword": {"reason": gen.error_message or "the provider refused the prompt"}}
        return True
    elif gen.status in (GenStatus.failed, GenStatus.canceled):
        step.status = StepStatus.failed
        step.error = gen.error_message or f"the take was {gen.status.value}"
    elif gen.status == GenStatus.blocked_budget:
        # over the daily or monthly cap, and nothing moves it on by itself: end the take, so the run says why
        # (and stops counting it) instead of waiting forever; a retry after raising the cap starts a new one
        reason = gen.status_reason or "over the spending cap"
        await fsm.transition(session, gen, GenStatus.canceled, reason=reason)
        step.status, step.error = StepStatus.failed, f"blocked by the budget: {reason}"
    else:
        return False
    step.finished_at = _now()
    return False


async def _start_generate(session: AsyncSession, run: PipelineRun, step: PipelineStep, fx: Effects) -> None:
    policy = await guard.get_policy(session, run.user_id)
    cap = run.cap_usd if run.cap_usd is not None else policy.default_run_cap_usd
    remaining = max(Decimal("0"), cap - await run_spent(session, run))
    body = CreateRequest(
        model_id=step.input["model_id"], mode=step.input["mode"], prompt=step.input.get("prompt", ""),
        params=step.input.get("params") or {}, media=step.input.get("media") or [],
        direction=step.input.get("direction"), group_id=run.id, pipeline_step_id=step.id,
        project_id=uuid.UUID(step.input["project_id"]) if step.input.get("project_id") else None,
        shot_key=step.input.get("shot_key"),
        # the run's cap is the confirmation and the limit: a take starts only if it fits in what's left, however cheap
        confirm_cost_usd=float(remaining), max_cost_usd=float(remaining),
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


def _apply_spec(step: PipelineStep, spec: StepSpec, sort: int, run_digest: str) -> None:
    step.kind, step.label, step.sort = spec.kind, spec.label, sort
    step.depends_on = list(spec.depends_on)
    # `_run`: the run's input it was planned from, so a finished step is redone only when that changes
    step.input = {**spec.input, **({"_optional": True} if spec.optional else {}), "_run": run_digest}
    step.input_hash = spec.digest()


def _only_the_catalog_moved(st: PipelineStep, steps: dict[str, PipelineStep], run_digest: str) -> bool:
    """A finished step whose plan changed although the run's input didn't and nothing it depends on was redone after
    it: only the registry or a catalog moved under it (a deploy). It keeps its work; redoing it would pay again."""
    if st.status != StepStatus.succeeded or st.input.get("_run") != run_digest or st.finished_at is None:
        return False
    deps = (steps.get(k) for k in st.depends_on or ())
    return not any(d and d.finished_at and d.finished_at > st.finished_at for d in deps)


def _input_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def _settle_after_end(session: AsyncSession, run: PipelineRun) -> None:
    """A run that failed while other takes were still rendering: they keep going (they're paid for, and a retry
    reuses them), so fold each one into its step when it finishes instead of leaving the step "running"."""
    rows = (await session.execute(select(PipelineStep).where(
        PipelineStep.run_id == run.id, PipelineStep.kind == StepKind.generate,
        PipelineStep.status == StepStatus.running, PipelineStep.generation_id.is_not(None))
        .with_for_update())).scalars().all()
    changed = False
    for st in rows:
        await _sync_generation(session, st)
        changed |= st.status != StepStatus.running
    if changed:
        await emit_run(session, run)


async def reconcile(session: AsyncSession, run: PipelineRun, fx: Effects) -> None:
    """Bring a locked run up to date and start ready steps. Caller commits and then applies `fx`."""
    if run.status in RUN_DONE:
        await _settle_after_end(session, run)
        return
    recipe = get_recipe(run.recipe)
    if recipe is None:
        run.status, run.error, run.finished_at = RunStatus.failed, f"unknown recipe {run.recipe!r}", _now()
        await emit_run(session, run)
        return
    reg = get_registry()
    rows = (await session.execute(select(PipelineStep).where(PipelineStep.run_id == run.id)
                                  .with_for_update())).scalars().all()
    steps = {st.key: st for st in rows}

    for st in steps.values():
        if st.kind == StepKind.generate and st.status == StepStatus.running and st.generation_id:
            # a refused take waiting for its rewording is (re)queued: the step task locks it and runs it once
            if "_reword" in st.input or await _sync_generation(session, st, may_reword=True):
                fx.steps.append(st.id)
        elif st.kind in (StepKind.local, StepKind.llm) and st.status == StepStatus.running:
            # its job was lost (a restart mid-op): queue it again. One still being run holds this row's lock, so
            # it can't be seen here as running; a copy already queued is dropped by the step's queueing lock.
            fx.steps.append(st.id)

    try:
        inp = recipe.Input.model_validate(run.input)
        specs = recipe.plan(inp, settled_outputs(steps), reg)
    except Exception as exc:  # a model renamed in the registry, an input the recipe no longer takes: say so
        log.exception("planning run %s failed", run.id)
        run.status, run.error, run.finished_at = RunStatus.failed, f"couldn't plan the run: {exc}"[:2000], _now()
        await emit_run(session, run)
        return
    run_digest = _input_digest(run.input)
    wanted: set[str] = set()
    for i, spec in enumerate(specs):
        wanted.add(spec.key)
        st = steps.get(spec.key)
        if st is None:
            st = PipelineStep(run_id=run.id, key=spec.key, status=StepStatus.pending)
            _apply_spec(st, spec, i, run_digest)
            session.add(st)
            steps[spec.key] = st
        elif st.input_hash != spec.digest() and st.status == StepStatus.waiting:
            # a checkpoint still waiting for its answer asks about the latest input (an edited script)
            _apply_spec(st, spec, i, run_digest)
        elif ((st.input_hash != spec.digest() or st.status == StepStatus.skipped) and st.status != StepStatus.running
              and not _only_the_catalog_moved(st, steps, run_digest)):
            # its inputs changed (an edited shot, an upstream redo), or it was skipped and is wanted again: do it
            _apply_spec(st, spec, i, run_digest)
            st.status, st.output, st.error, st.generation_id = StepStatus.pending, {}, None, None
            st.started_at = st.finished_at = None
        else:
            st.sort, st.label = i, spec.label
            if st.input_hash == spec.digest() and st.input.get("_run") != run_digest:
                # unchanged by a revision of the run: it was planned from the new input too
                st.input = {**st.input, "_run": run_digest}
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
    finished = all(st.status in STEP_DONE for st in live)
    if finished and (why := recipe.failure(inp, settled)):
        run.status, run.error, run.finished_at = RunStatus.failed, why[:2000], _now()
    elif result is not None and finished:
        run.status, run.output, run.finished_at = RunStatus.succeeded, result, _now()
    elif any(st.status == StepStatus.waiting for st in live) and not any(
            st.status == StepStatus.running for st in live):
        run.status = RunStatus.waiting
    else:
        run.status = RunStatus.running
    if run.status != previous or fx.submit or fx.steps:
        await emit_run(session, run)


async def execute_step(session: AsyncSession, step: PipelineStep, run: PipelineRun) -> list[uuid.UUID]:
    """Run one local/llm step in place (or reword a refused take's prompt). Returns assets that need derivatives.
    Caller commits."""
    token = CURRENT_RUN.set(run.id)  # Claude calls made now are charged to this run
    try:
        return await _execute_step(session, step, run)
    finally:
        CURRENT_RUN.reset(token)


async def _execute_step(session: AsyncSession, step: PipelineStep, run: PipelineRun) -> list[uuid.UUID]:
    if step.kind == StepKind.generate:
        await _reword(session, step, run)
        return []
    ops = {StepKind.local: LOCAL_OPS, StepKind.llm: LLM_OPS}.get(step.kind, {})
    op = ops.get(step.input.get("op", ""))
    try:
        if op is None:
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


async def _reword(session: AsyncSession, step: PipelineStep, run: PipelineRun) -> None:
    """Ask Claude for gentler prompts and put the step back in line with them. Its input hash stays the recipe's, so
    the plan keeps it; a real change to its input replaces the reworded prompts with the new ones."""
    reason = (step.input.get("_reword") or {}).get("reason", "")
    rest = {k: v for k, v in step.input.items() if k != "_reword"}
    texts = _texts(step)
    before = [text for *_, text in texts]
    try:
        new = await reword(session, run, before, reason)
    except StepFailed as exc:
        step.input = {**rest, "_reworded": {"from": before, "failed": str(exc)}}
        step.status, step.finished_at = StepStatus.failed, _now()
        step.error = f"{reason or 'refused'} (a reworded try wasn't possible: {exc})"[:2000]
        return
    step.input = {**_with_texts(rest, texts, new.prompts), "_reworded": {"from": before, "why": new.changed}}
    step.status, step.generation_id, step.error = StepStatus.pending, None, None


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
