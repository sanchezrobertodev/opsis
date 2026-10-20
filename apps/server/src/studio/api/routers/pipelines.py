from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import (
    CheckpointAnswer,
    ConfirmationProblem,
    RecipeOut,
    RunCreate,
    RunEstimateOut,
    RunOut,
    RunRevise,
    RunSummary,
    StepOut,
    ValidationProblem,
)
from studio.api.serializers import asset_out
from studio.budget import guard
from studio.db.models import (
    Asset,
    Generation,
    PipelineRun,
    PipelineStep,
    RunStatus,
    StepKind,
    StepStatus,
    User,
)
from studio.domain.generations import (
    ConfirmationRequired,
    GenerationRequestError,
    quick_estimate,
    resolve_media_kinds,
)
from studio.pipelines.engine import (
    RUN_DONE,
    Effects,
    cancel_run,
    emit_run,
    resolve_checkpoint,
    retry_run,
    run_spent,
)
from studio.pipelines.recipe import Recipe
from studio.pipelines.recipes import RECIPES, get_recipe
from studio.registry.loader import get_registry
from studio.worker.tasks.pipeline import apply, defer_advance

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("/recipes", response_model=list[RecipeOut])
async def list_recipes() -> list[RecipeOut]:
    return [RecipeOut(id=r.id, name=r.name, summary=r.summary, input_schema=r.Input.model_json_schema())
            for r in RECIPES.values()]


def _recipe(recipe_id: str) -> Recipe:
    recipe = get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(404, f"unknown recipe {recipe_id!r}")
    return recipe


async def _estimate(session: AsyncSession, user: User, body: RunCreate) -> tuple[Recipe, object, RunEstimateOut]:
    recipe = _recipe(body.recipe)
    try:
        inp = recipe.Input.model_validate(body.input)
    except ValidationError as exc:
        raise GenerationRequestError(
            [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]) from None
    reg = get_registry()
    inp = await recipe.resolve(session, user.id, inp, reg)  # first, so validate sees what it filled in
    if errors := recipe.validate(inp, reg):
        raise GenerationRequestError(errors)
    total, live, live_known = Decimal("0"), Decimal("0"), True
    by_key: dict[str, Decimal] = {}
    live_by_key: dict[str, Decimal] = {}
    jobs = recipe.jobs(inp, reg)
    for job in jobs:
        known = await resolve_media_kinds(session, user.id, [{"role": r, "asset_id": a} for r, a in job.media])             if job.media else None
        usd, live_usd = await quick_estimate(session, job.model_id, job.mode, job.params, list(job.roles), reg,
                                             prompt="x" * job.prompt_chars if job.prompt_chars else "planned",
                                             known=known, seconds=dict(job.planned_seconds))
        total += usd
        if job.key is not None:
            by_key[job.key] = by_key.get(job.key, Decimal("0")) + usd
        if live_usd is None:
            live_known = False
        else:
            live += live_usd
            if job.key is not None:
                live_by_key[job.key] = live_by_key.get(job.key, Decimal("0")) + live_usd
    policy = await guard.get_policy(session, user.id)
    cap = Decimal(str(body.cap_usd)) if body.cap_usd is not None else policy.default_run_cap_usd
    return recipe, inp, RunEstimateOut(
        estimate_usd=float(total), live_estimate_usd=float(live) if live_known else None, generations=len(jobs),
        cap_usd=float(cap), by_key={k: float(v) for k, v in by_key.items()},
        live_by_key={k: float(v) for k, v in live_by_key.items()} if live_known else None)


@router.post("/estimate", response_model=RunEstimateOut, responses={422: {"model": ValidationProblem}})
async def estimate_run(body: RunCreate, session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> RunEstimateOut:
    """What a run would cost, without starting it."""
    _, _, est = await _estimate(session, user, body)
    return est


@router.post("", response_model=RunOut, status_code=201,
             responses={409: {"model": ConfirmationProblem}, 422: {"model": ValidationProblem}})
async def start_run(body: RunCreate, session: AsyncSession = Depends(get_session),
                    user: User = Depends(current_user)) -> RunOut:
    recipe, inp, est = await _estimate(session, user, body)
    estimate = Decimal(str(est.estimate_usd))
    if estimate > Decimal(str(est.cap_usd)):
        raise GenerationRequestError([f"this run is estimated at ${estimate:.2f}, over its ${est.cap_usd:.2f} cap; "
                                      "raise the cap to start it"])
    policy = await guard.get_policy(session, user.id)
    if guard.needs_confirmation(estimate, policy.confirm_over_usd, body.confirm_cost_usd):
        raise ConfirmationRequired(estimate, f"recipe:{recipe.id}", policy.confirm_over_usd)
    run = PipelineRun(user_id=user.id, recipe=recipe.id, title=(body.title or recipe.title(inp))[:200],
                      input=inp.model_dump(mode="json"), cap_usd=Decimal(str(est.cap_usd)), estimate_usd=estimate)
    session.add(run)
    await session.flush()
    await emit_run(session, run)
    await session.commit()
    await defer_advance(run.id)
    return await run_out(session, run)


@router.get("", response_model=list[RunSummary])
async def list_runs(limit: int = Query(30, ge=1, le=100), recipe: str | None = None,
                    channel_id: uuid.UUID | None = Query(None, description="Faceless episodes of one channel"),
                    project_id: uuid.UUID | None = Query(None, description="Runs for one Cinema project"),
                    status: RunStatus | None = Query(None, description="Only runs in this status, e.g. running"),
                    session: AsyncSession = Depends(get_session),
                    user: User = Depends(current_user)) -> list[RunSummary]:
    q = select(PipelineRun).where(PipelineRun.user_id == user.id)
    if recipe:
        q = q.where(PipelineRun.recipe == recipe)
    if channel_id:
        q = q.where(PipelineRun.input["channel_id"].astext == str(channel_id))
    if project_id:
        q = q.where(PipelineRun.input["project_id"].astext == str(project_id))
    if status:
        q = q.where(PipelineRun.status == status)
    runs = (await session.execute(q.order_by(PipelineRun.created_at.desc()).limit(limit))).scalars().all()
    counts = {rid: (done, total) for rid, done, total in (await session.execute(
        select(PipelineStep.run_id,
               func.count().filter(PipelineStep.status.in_([StepStatus.succeeded, StepStatus.skipped])),
               func.count().filter(PipelineStep.status != StepStatus.skipped))
        .where(PipelineStep.run_id.in_([r.id for r in runs])).group_by(PipelineStep.run_id))).all()}
    outputs = await _assets(session, user.id, [r.output.get("asset_id") for r in runs])
    return [_summary(r, outputs, counts.get(r.id, (0, 0))) for r in runs]


async def _assets(session: AsyncSession, user_id: uuid.UUID, ids: list[str | None]) -> dict[str, Asset]:
    wanted = {uuid.UUID(i) for i in ids if i}
    if not wanted:
        return {}
    return {str(a.id): a for a in (await session.execute(select(Asset).where(
        Asset.id.in_(wanted), Asset.user_id == user_id, Asset.deleted_at.is_(None)))).scalars()}


def _summary(run: PipelineRun, assets: dict[str, Asset], counts: tuple[int, int]) -> RunSummary:
    recipe = get_recipe(run.recipe)
    out = assets.get(run.output.get("asset_id") or "")
    return RunSummary(
        id=run.id, recipe=run.recipe, recipe_name=recipe.name if recipe else run.recipe, title=run.title,
        status=run.status.value, error=run.error,
        estimate_usd=float(run.estimate_usd) if run.estimate_usd is not None else None,
        cap_usd=float(run.cap_usd) if run.cap_usd is not None else None, created_at=run.created_at,
        finished_at=run.finished_at, output_asset=asset_out(out) if out else None,
        steps_done=counts[0], steps_total=counts[1],
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def run_out(session: AsyncSession, run: PipelineRun) -> RunOut:
    steps = (await session.execute(select(PipelineStep).where(PipelineStep.run_id == run.id)
                                   .order_by(PipelineStep.sort, PipelineStep.created_at))).scalars().all()
    gens = {g.id: g for g in (await session.execute(select(Generation).where(
        Generation.id.in_([s.generation_id for s in steps if s.generation_id])))).scalars()} if steps else {}
    ids: list[str | None] = [run.output.get("asset_id")]
    for st in steps:
        ids += [st.output.get("asset_id"), *st.output.get("asset_ids", [])]
        if st.kind == StepKind.checkpoint:  # pick options are asset ids; approve options are plain words
            ids += [v for o in st.input.get("options", []) if _is_uuid(v := str(o.get("value")))]
    assets = await _assets(session, run.user_id, ids)

    def step_out(st: PipelineStep) -> StepOut:
        gen = gens.get(st.generation_id) if st.generation_id else None
        mine = st.output.get("asset_ids") or ([st.output["asset_id"]] if st.output.get("asset_id") else [])
        options = [{**o, "asset": asset_out(assets[o["value"]]).model_dump(mode="json") if o.get("value") in assets else None}
                   for o in st.input.get("options", [])]
        return StepOut(
            key=st.key, kind=st.kind.value, label=st.label, status=st.status.value, depends_on=st.depends_on,
            error=st.error, output=st.output, options=options, generation_id=st.generation_id,
            generation_status=gen.status.value if gen else None, progress=gen.progress if gen else None,
            assets=[asset_out(assets[i]) for i in mine if i in assets],
            started_at=st.started_at, finished_at=st.finished_at,
        )

    visible = [st for st in steps if st.status != StepStatus.skipped]
    done = sum(st.status == StepStatus.succeeded for st in visible)
    base = _summary(run, assets, (done, len(visible)))
    return RunOut(**base.model_dump(), input=run.input, output=run.output,
                  spent_usd=float(await run_spent(session, run)), steps=[step_out(st) for st in visible])


async def _owned(session: AsyncSession, user: User, run_id: uuid.UUID, lock: bool = False) -> PipelineRun:
    run = await session.get(PipelineRun, run_id, with_for_update=lock)
    if run is None or run.user_id != user.id:
        raise HTTPException(404, "run not found")
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                  user: User = Depends(current_user)) -> RunOut:
    return await run_out(session, await _owned(session, user, run_id))


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel(run_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                 user: User = Depends(current_user)) -> RunOut:
    run = await _owned(session, user, run_id, lock=True)
    fx = Effects()
    # a failed run can still have takes rendering (they're kept for a retry): canceling stops them too
    if run.status not in (RunStatus.succeeded, RunStatus.canceled):
        await cancel_run(session, run, fx)
    await session.commit()
    await apply(fx, run.id)
    return await run_out(session, run)


@router.post("/{run_id}/retry", response_model=RunOut)
async def retry(run_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                user: User = Depends(current_user)) -> RunOut:
    """Try the failed steps again. Finished steps are kept, so only what failed is redone (and paid for)."""
    run = await _owned(session, user, run_id, lock=True)
    if run.status not in (RunStatus.failed, RunStatus.canceled):
        raise HTTPException(409, "only failed or canceled runs can be retried")
    await retry_run(session, run)
    await session.commit()
    await defer_advance(run.id)
    return await run_out(session, run)


@router.post("/{run_id}/revise", response_model=RunOut, responses={422: {"model": ValidationProblem}})
async def revise(run_id: uuid.UUID, body: RunRevise, session: AsyncSession = Depends(get_session),
                 user: User = Depends(current_user)) -> RunOut:
    """Change a run's input (e.g. rewrite shot 3). Steps whose inputs are unchanged keep their results;
    only the changed ones, and whatever depends on them, run again."""
    run = await _owned(session, user, run_id, lock=True)
    recipe = _recipe(run.recipe)
    try:
        inp = recipe.Input.model_validate(body.input)
    except ValidationError as exc:
        raise GenerationRequestError(
            [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]) from None
    inp = await recipe.resolve(session, user.id, inp, get_registry())
    if errors := recipe.validate(inp, get_registry()):
        raise GenerationRequestError(errors)
    if run.status == RunStatus.canceled:
        raise HTTPException(409, "a canceled run can't be revised; start a new one")
    run.input = inp.model_dump(mode="json")
    if run.status in RUN_DONE:
        run.status, run.error, run.finished_at, run.output = RunStatus.running, None, None, {}
    await emit_run(session, run)
    await session.commit()
    await defer_advance(run.id)
    return await run_out(session, run)


@router.post("/{run_id}/checkpoints/{key}", response_model=RunOut)
async def answer_checkpoint(run_id: uuid.UUID, key: str, body: CheckpointAnswer,
                            session: AsyncSession = Depends(get_session),
                            user: User = Depends(current_user)) -> RunOut:
    run = await _owned(session, user, run_id, lock=True)
    try:
        await resolve_checkpoint(session, run, key, body.choice)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    await defer_advance(run.id)
    return await run_out(session, run)
