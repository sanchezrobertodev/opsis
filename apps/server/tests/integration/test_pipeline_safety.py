"""The engine never pays twice for work a run already has: after a deploy, around a cancel, and for a stale job."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from studio.api.main import app as api
from studio.config import get_settings
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
from studio.db.session import session_scope
from studio.domain.enums import AssetKind, AssetSource, AssetStatus, AttemptStatus, GenStatus, LedgerEntry
from studio.pipelines import engine
from studio.pipelines.recipes import get_recipe
from studio.worker.app import app as worker
from studio.worker.tasks.pipeline import advance, defer_advance

SHOTS = {"model_id": "kling-3.0", "shots": [{"prompt": "a"}, {"prompt": "b"}]}
VEO = {"model_id": "veo-3.1", "params": {"duration": 4}}


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def _gens(run_id: str) -> int:
    query = select(func.count()).select_from(Generation).where(Generation.group_id == uuid.UUID(run_id))
    async with session_scope() as s:
        return (await s.execute(query)).scalar_one()


def _deploy(monkeypatch, recipe_id: str) -> None:
    """What a changed model YAML does to every take's input (a new default param)."""
    recipe = get_recipe(recipe_id)
    plan = type(recipe).plan

    def deployed(self, inp, settled, reg):
        specs = plan(self, inp, settled, reg)
        for sp in specs:
            if sp.kind == StepKind.generate:
                sp.input["params"] = {**sp.input.get("params", {}), "new_default": 1}
        return specs

    monkeypatch.setattr(type(recipe), "plan", deployed)


async def _revise(client, run_id: str, inp: dict) -> None:
    r = await client.post(f"/pipelines/{run_id}/revise", json={"input": inp})
    assert r.status_code == 200, r.text


async def test_revising_a_finished_run_after_a_deploy_redoes_only_what_it_changes(
    client, in_memory_queue, monkeypatch
):
    from tests.integration.test_pipelines import drain, get, start  # importable once the suite is collected

    inp = {**SHOTS, "assemble": False}
    run = await start(client, "sequence", **inp)
    await drain(in_memory_queue)
    before = {s["key"]: s["generation_id"] for s in (await get(client, run["id"]))["steps"]}
    assert await _gens(run["id"]) == 2

    _deploy(monkeypatch, "sequence")
    await _revise(client, run["id"], {**inp, "assemble": True})  # touches no shot
    await drain(in_memory_queue)
    after = await get(client, run["id"])
    assert after["status"] == "succeeded", after["error"]
    assert "cut" in {s["key"] for s in after["steps"]} and await _gens(run["id"]) == 2  # nothing paid twice

    await _revise(
        client, run["id"], {**inp, "assemble": True, "shots": [{"prompt": "a"}, {"prompt": "a storm"}]}
    )
    await drain(in_memory_queue)
    steps = {s["key"]: s["generation_id"] for s in (await get(client, run["id"]))["steps"]}
    # the edited shot is redone, the other kept
    assert steps["shot[01]"] == before["shot[01]"] and steps["shot[02]"] != before["shot[02]"]
    assert await _gens(run["id"]) == 3


async def test_a_waiting_run_takes_a_deploys_plan_so_a_later_revision_keeps_its_takes(
    client, in_memory_queue, monkeypatch
):
    from tests.integration.test_pipelines import drain, get, start  # importable once the suite is collected

    entries = [{"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}]
    run = await start(client, "compare", prompt="a paper boat", entries=entries)
    await drain(in_memory_queue)
    assert (await get(client, run["id"]))["status"] == "waiting" and await _gens(run["id"]) == 2

    _deploy(monkeypatch, "compare")
    await defer_advance(uuid.UUID(run["id"]))  # its next wake-up, after the deploy
    await drain(in_memory_queue)
    await _revise(client, run["id"], {"prompt": "a paper boat", "entries": [*entries, VEO]})
    await drain(in_memory_queue)
    assert await _gens(run["id"]) == 3  # only the new entry's take


async def test_a_deploy_while_takes_render_then_a_revision_keeps_them(client, in_memory_queue, monkeypatch):
    from tests.integration.test_pipelines import drain, start

    entries = [{"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}]
    run = await start(client, "compare", prompt="a paper boat", entries=entries)
    # only the advance: the takes are made and left rendering
    for job in sorted(in_memory_queue.jobs.values(), key=lambda j: j["id"]):
        if job["status"] == "todo" and job["task_name"] == "pipeline.advance":
            job["status"] = "doing"
            await worker.tasks[job["task_name"]].func(**job["args"])
            job["status"] = "succeeded"
    assert await _gens(run["id"]) == 2

    _deploy(monkeypatch, "compare")
    await _revise(client, run["id"], {"prompt": "a paper boat", "entries": [*entries, VEO]})
    await drain(in_memory_queue)
    assert await _gens(run["id"]) == 3  # the two rendering takes are kept


async def test_revising_two_shots_of_a_chain_makes_each_once_from_the_new_frame(client, in_memory_queue):
    from tests.integration.test_pipelines import drain, start

    inp = {**SHOTS, "assemble": False}
    run = await start(client, "sequence", **inp)
    await drain(in_memory_queue)
    await _revise(client, run["id"], {**inp, "shots": [{"prompt": "a2"}, {"prompt": "b2"}]})
    await drain(in_memory_queue)
    # shot 2 waited for shot 1's new last frame instead of starting (and being paid for) from the old one
    assert await _gens(run["id"]) == 4
    query = select(PipelineStep).where(PipelineStep.run_id == uuid.UUID(run["id"]))
    async with session_scope() as s:
        steps = {st.key: st for st in (await s.execute(query)).scalars()}
    frame = steps["frame[01]"].output["asset_id"]
    assert frame in {str(m.get("asset_id")) for m in steps["shot[02]"].input["media"]}


def test_a_take_whose_frame_was_remade_while_it_rendered_isnt_kept_as_a_deploys():
    t = datetime(2026, 11, 2, tzinfo=UTC)
    shot = SimpleNamespace(
        status=StepStatus.succeeded,
        input={"_run": "d"},
        depends_on=["frame[01]"],
        started_at=t,
        finished_at=t + timedelta(minutes=5),
    )
    frame = SimpleNamespace(
        finished_at=t + timedelta(minutes=1)
    )  # remade after the take started from the old one
    assert not engine._only_the_catalog_moved(shot, {"frame[01]": frame}, "d")
    frame.finished_at = t - timedelta(minutes=1)  # made before it: only a deploy moved its plan
    assert engine._only_the_catalog_moved(shot, {"frame[01]": frame}, "d")


async def _take(s, run: PipelineRun, status: GenStatus, *, held: bool = False) -> Generation:
    """`held`: its provider's answer couldn't be read, so its attempt is unknown and its money reserved."""
    kw = {"model_id": "kling-3.0", "mode": "t2v", "output_type": "video", "request": {}}
    g = Generation(
        user_id=run.user_id, group_id=run.id, status=status, estimated_cost_usd=Decimal("0.70"), **kw
    )
    s.add(g)
    await s.flush()
    if held:
        a = GenerationAttempt(
            generation_id=g.id,
            attempt_no=1,
            route_id="kling-3.0@fal",
            provider="fal",
            status=AttemptStatus.unknown,
        )
        s.add(a)
        await s.flush()
        s.add(
            SpendLedger(
                user_id=run.user_id,
                entry_type=LedgerEntry.reservation,
                amount_usd=Decimal("0.70"),
                provider="fal",
                route_id=a.route_id,
                generation_attempt_id=a.id,
                estimated=True,
            )
        )
    return g


async def _released(gen_id: uuid.UUID) -> bool:
    query = (
        select(SpendLedger.id)
        .join(GenerationAttempt, SpendLedger.generation_attempt_id == GenerationAttempt.id)
        .where(GenerationAttempt.generation_id == gen_id, SpendLedger.entry_type == LedgerEntry.release)
    )
    async with session_scope() as s:
        return (await s.execute(query)).first() is not None


async def _spent(run_id: uuid.UUID) -> Decimal:
    async with session_scope() as s:
        return await engine.run_spent(s, await s.get(PipelineRun, run_id))


async def test_canceling_a_run_leaves_takes_that_cant_be_stopped_to_settle_and_retry_keeps_them(client):
    """A take being saved (ingesting) can't be canceled, which used to fail the whole cancel; one whose outcome is
    unknown used to lose its held money, so a retry paid for it again."""
    from tests.integration.test_pipelines import get

    async with session_scope() as s:
        run = PipelineRun(
            user_id=get_settings().owner_user_id,
            recipe="sequence",
            title="three takes",
            status=RunStatus.running,
            input=SHOTS,
        )
        s.add(run)
        await s.flush()
        saving = await _take(s, run, GenStatus.ingesting)
        held = await _take(s, run, GenStatus.unknown, held=True)
        queued = await _take(s, run, GenStatus.queued)
        for i, g in enumerate([saving, held, queued], 1):
            s.add(
                PipelineStep(
                    run_id=run.id,
                    key=f"shot[{i:02d}]",
                    kind=StepKind.generate,
                    input={},
                    sort=i,
                    input_hash="x",
                    status=StepStatus.running,
                    generation_id=g.id,
                )
            )
        await s.commit()
        run_id, ids = run.id, [saving.id, held.id, queued.id]

    r = await client.post(f"/pipelines/{run_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "canceled"
    assert [st["status"] for st in r.json()["steps"]] == ["running", "running", "canceled"]
    async with session_scope() as s:
        statuses = [(await s.get(Generation, i)).status for i in ids]
    assert statuses == [GenStatus.ingesting, GenStatus.unknown, GenStatus.canceled]
    assert await _spent(run_id) == Decimal("1.40") and not await _released(ids[1])  # both may still be billed

    async with session_scope() as s:  # the save finishes: its take lands in its step
        g = await s.get(Generation, ids[0])
        g.status = GenStatus.succeeded
        s.add(
            Asset(
                user_id=g.user_id,
                kind=AssetKind.video,
                status=AssetStatus.ready,
                source=AssetSource.generation,
                mime="video/mp4",
                storage_key=f"test/{g.id}.mp4",
                generation_id=g.id,
                output_index=0,
            )
        )
        await s.commit()
    await advance(run_id)
    settled = (await get(client, str(run_id)))["steps"]
    assert [st["status"] for st in settled] == ["succeeded", "running", "canceled"]

    r = await client.post(f"/pipelines/{run_id}/retry")
    assert r.status_code == 200, r.text
    steps = r.json()["steps"]
    # only the canceled take is made again
    assert [st["status"] for st in steps] == ["succeeded", "running", "pending"]
    assert [st["generation_id"] for st in steps[:2]] == [str(ids[0]), str(ids[1])]
    # the held take waits for its check; marking it failed releases its money
    assert (await client.post(f"/generations/{ids[1]}/resolve-unknown")).status_code == 200
    assert await _released(ids[1])


async def test_a_take_whose_outcome_is_unknown_keeps_its_money_held_until_checked(client):
    async with session_scope() as s:
        run = PipelineRun(
            user_id=get_settings().owner_user_id,
            recipe="sequence",
            title="held",
            status=RunStatus.failed,
            input=SHOTS,
        )
        s.add(run)
        await s.flush()
        unknown = await _take(s, run, GenStatus.unknown, held=True)
        # canceled (or marked failed) while it was being sent, and the send got no answer
        sent = await _take(s, run, GenStatus.canceled, held=True)
        failed = await _take(s, run, GenStatus.failed, held=True)
        await s.commit()
        run_id, ids = run.id, [unknown.id, sent.id, failed.id]

    r = await client.post(f"/generations/{ids[0]}/cancel")
    assert r.status_code == 409, r.text  # only a check at the provider settles it
    # all may still be billed: their money stays held, and the run's cap counts it so a retry can't spend it again
    assert await _spent(run_id) == Decimal("2.10") and not await _released(ids[0])
    shown = [(await client.get(f"/generations/{i}")).json() for i in ids]
    assert [g["held"] for g in shown] == [False, True, True]  # the stopped ones say so, so the take offers the check
    r = await client.delete(f"/generations/{ids[1]}")
    assert r.status_code == 409, r.text  # once removed, nothing could release its money

    for gen_id in ids:  # checked at the provider: none ran
        r = await client.post(f"/generations/{gen_id}/resolve-unknown")
        assert r.status_code == 200, r.text
        assert await _released(gen_id)
    assert await _spent(run_id) == 0


async def test_a_bill_booked_on_a_take_whose_outcome_is_unknown_counts_instead_of_its_estimate():
    """Reconcile can book the provider's bill on an attempt still parked unknown: the run's cap counts it once."""
    async with session_scope() as s:
        run = PipelineRun(user_id=get_settings().owner_user_id, recipe="sequence", title="billed while held",
                          status=RunStatus.running, input=SHOTS)
        s.add(run)
        await s.flush()
        unknown = await _take(s, run, GenStatus.unknown, held=True)
        canceled = await _take(s, run, GenStatus.canceled, held=True)
        bills = {unknown.id: Decimal("0.42"), canceled.id: Decimal("0.30")}
        for a in (await s.execute(select(GenerationAttempt).where(GenerationAttempt.generation_id.in_(bills)))).scalars():
            s.add(SpendLedger(user_id=run.user_id, entry_type=LedgerEntry.adjustment, amount_usd=bills[a.generation_id],
                              provider="fal", route_id="kling-3.0@fal", generation_attempt_id=a.id))
        await s.commit()
        run_id = run.id
    assert await _spent(run_id) == Decimal("0.72")  # the bills, not the bills and the estimates


async def test_a_stale_step_job_never_rewords_a_take_already_reworded(monkeypatch):
    """Two advances can queue the step twice; the second copy finds the reworded take rendering and leaves it."""

    async def reword(*args):
        raise AssertionError("reworded twice")

    monkeypatch.setattr(engine, "reword", reword)
    gen_id = uuid.uuid4()
    reworded = {
        "model_id": "veo-3.1",
        "prompt": "a gentle fall",
        "_reworded": {"from": ["a fall"], "why": "softer"},
    }
    step = SimpleNamespace(
        kind=StepKind.generate,
        key="shot[01]",
        id=uuid.uuid4(),
        status=StepStatus.running,
        generation_id=gen_id,
        input=dict(reworded),
    )
    assert await engine.execute_step(None, step, SimpleNamespace(id=uuid.uuid4())) == []  # type: ignore[arg-type]
    assert step.status == StepStatus.running and step.generation_id == gen_id and step.input == reworded
