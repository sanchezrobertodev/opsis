"""Recipe runs end to end on the mock provider: the real API, engine, worker tasks and ffmpeg.

`drain()` plays the worker: it runs queued jobs through their real task functions until the queue is
empty, giving after-commit wake-ups a moment to land between rounds.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Asset, AssetLineage, Generation
from studio.db.session import session_scope
from studio.media.ffmpeg import probe
from studio.storage.local import get_storage
from studio.worker.app import app as worker


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def drain(queue, rounds: int = 300) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.02)
        todo = sorted((j for j in queue.jobs.values() if j["status"] == "todo"), key=lambda j: j["id"])
        if not todo:
            await asyncio.sleep(0.05)
            if not any(j["status"] == "todo" for j in queue.jobs.values()):
                return
            continue
        for job in todo:
            job["status"] = "doing"
            await worker.tasks[job["task_name"]].func(**job["args"])
            job["status"] = "succeeded"
    raise AssertionError("queue never drained")


async def start(client, recipe: str, **inp) -> dict:
    r = await client.post("/pipelines", json={"recipe": recipe, "input": inp})
    assert r.status_code == 201, r.text
    return r.json()


async def get(client, run_id: str) -> dict:
    return (await client.get(f"/pipelines/{run_id}")).json()


async def test_sequence_chains_last_frames_and_joins_the_cut(client, in_memory_queue):
    run = await start(client, "sequence", model_id="veo-3.1", shots=[
        {"prompt": "a lighthouse at dusk", "duration": 4},
        {"prompt": "the lamp turns on", "duration": 4},
        {"prompt": "waves crash below", "duration": 4},
    ])
    assert run["status"] == "running" and run["estimate_usd"] == 0  # mock routes are free
    await drain(in_memory_queue)

    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert [s["key"] for s in done["steps"]] == [
        "shot[01]", "frame[01]", "shot[02]", "frame[02]", "shot[03]", "cut"]
    assert all(s["status"] == "succeeded" for s in done["steps"])

    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        shot2 = await s.get(Generation, uuid.UUID(steps["shot[02]"]["generation_id"]))
        assert shot2.mode == "i2v"
        assert [(m["role"], m["asset_id"]) for m in shot2.request["media"]] == [
            ("start_image", steps["frame[01]"]["output"]["asset_id"])]
        cut = await s.get(Asset, uuid.UUID(done["output"]["asset_id"]))
        parents = (await s.execute(select(AssetLineage).where(AssetLineage.child_asset_id == cut.id))).scalars().all()
    assert len(parents) == 3 and cut.source.value == "pipeline"
    info = await probe(get_storage().path_for(cut.storage_key))
    assert info.duration_ms == pytest.approx(12_000, abs=600) and info.has_audio
    assert done["output_asset"]["id"] == done["output"]["asset_id"]

    listed = (await client.get("/pipelines")).json()
    assert listed[0]["id"] == run["id"] and listed[0]["steps_done"] == listed[0]["steps_total"] == 6


async def test_compare_waits_for_a_pick_and_survives_a_failed_take(client, in_memory_queue):
    run = await start(client, "compare", prompt="a paper boat in the rain", entries=[
        {"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}, {"model_id": "veo-3.1", "params": {"duration": 4}},
    ])
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting"
    pick = next(s for s in waiting["steps"] if s["key"] == "pick")
    assert pick["status"] == "waiting" and len(pick["options"]) == 3
    assert {o["label"] for o in pick["options"]} == {"Kling 3.0", "Seedance 2.5", "Veo 3.1"}

    bad = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": str(uuid.uuid4())})
    assert bad.status_code == 409
    choice = pick["options"][1]["value"]
    r = await client.post(f"/pipelines/{run['id']}/checkpoints/pick", json={"choice": choice})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded" and done["output"]["asset_id"] == choice
    assert (await client.get(f"/assets/{choice}")).json()["favorite"] is True

    # one model failing doesn't sink a comparison; with a single good take there is nothing to pick
    run = await start(client, "compare", prompt="a kite [[mock:fail=invalid_request]]", entries=[
        {"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}])
    await drain(in_memory_queue)
    failed_both = await get(client, run["id"])
    assert failed_both["status"] == "succeeded" and failed_both["output"]["asset_id"] is None
    assert all(t["error"] for t in failed_both["output"]["takes"])


async def test_a_failed_shot_fails_the_run_and_retry_redoes_only_it(client, in_memory_queue):
    run = await start(client, "sequence", model_id="kling-3.0", assemble=False, shots=[
        {"prompt": "a fox in the snow"}, {"prompt": "the fox leaps [[mock:fail=invalid_request]]"}])
    await drain(in_memory_queue)
    failed = await get(client, run["id"])
    assert failed["status"] == "failed" and failed["error"].startswith("Shot 2:")
    first_gen = failed["steps"][0]["generation_id"]

    r = await client.post(f"/pipelines/{run['id']}/retry")
    assert r.status_code == 200
    await drain(in_memory_queue)
    again = await get(client, run["id"])
    assert again["status"] == "failed"  # the prompt still fails
    assert again["steps"][0]["generation_id"] == first_gen  # shot 1 was kept, not paid for twice


async def test_cancel_stops_the_run_and_its_takes(client, in_memory_queue):
    run = await start(client, "sequence", model_id="kling-3.0", shots=[{"prompt": "a"}, {"prompt": "b"}])
    await worker.tasks["pipeline.advance"].func(run_id=run["id"])  # shot 1 is created but not submitted
    r = await client.post(f"/pipelines/{run['id']}/cancel")
    assert r.json()["status"] == "canceled"
    gen_id = r.json()["steps"][0]["generation_id"]
    async with session_scope() as s:
        assert (await s.get(Generation, uuid.UUID(gen_id))).status.value == "canceled"


async def test_bad_input_is_rejected_before_anything_runs(client):
    r = await client.post("/pipelines", json={"recipe": "sequence", "input": {
        "model_id": "nano-banana-2", "shots": [{"prompt": "x"}]}})
    assert r.status_code == 422 and "needs a video model" in r.json()["errors"][0]
    r = await client.post("/pipelines", json={"recipe": "compare", "input": {
        "prompt": "x", "entries": [{"model_id": "veo-3.1"}, {"model_id": "nano-banana-2"}]}})
    assert r.status_code == 422 and "same kind of output" in r.json()["errors"][0]
    r = await client.post("/pipelines", json={"recipe": "sequence", "input": {"model_id": "veo-3.1", "shots": []}})
    assert r.status_code == 422
    est = await client.post("/pipelines/estimate", json={"recipe": "sequence", "input": {
        "model_id": "veo-3.1", "shots": [{"prompt": "x", "duration": 8}, {"prompt": "y", "duration": 8}]}})
    assert est.status_code == 200
    assert est.json()["generations"] == 2 and est.json()["live_estimate_usd"] == pytest.approx(2 * 3.2)


async def test_revising_a_shot_redoes_only_that_shot_and_the_cut(client, in_memory_queue):
    shots = [{"prompt": "dawn over a harbor"}, {"prompt": "a ferry leaves"}, {"prompt": "gulls follow it"}]
    run = await start(client, "sequence", model_id="kling-3.0", shots=shots)
    await drain(in_memory_queue)
    before = {s["key"]: s for s in (await get(client, run["id"]))["steps"]}

    shots[2]["prompt"] = "a storm rolls in behind it"
    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {"model_id": "kling-3.0", "shots": shots}})
    assert r.status_code == 200 and r.json()["status"] == "running"
    await drain(in_memory_queue)
    after = await get(client, run["id"])
    assert after["status"] == "succeeded"
    steps = {s["key"]: s for s in after["steps"]}
    for key in ("shot[01]", "frame[01]", "shot[02]", "frame[02]"):
        assert steps[key]["output"] == before[key]["output"], key  # untouched
    assert steps["shot[03]"]["generation_id"] != before["shot[03]"]["generation_id"]
    assert steps["cut"]["output"]["asset_id"] != before["cut"]["output"]["asset_id"]
    assert after["output"]["asset_id"] == steps["cut"]["output"]["asset_id"]


async def test_character_shot_renders_a_keyframe_from_references_then_animates_it(client, in_memory_queue):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (512, 512), (30, 90, 160)).save(buf, "PNG")
    up = (await client.post("/uploads", files={"file": ("ref.png", buf.getvalue(), "image/png")})).json()
    name = f"Odile {uuid.uuid4().hex[:6]}"
    el = (await client.post("/elements", json={"name": name, "description": "a dancer in a white dress",
                                               "images": [{"asset_id": up["id"], "view": "front"}]})).json()

    no_mention = await client.post("/pipelines", json={"recipe": "keyframe", "input": {
        "model_id": "kling-3.0", "prompt": "a dancer spins"}})
    assert no_mention.status_code == 422 and "@name" in no_mention.json()["errors"][0]

    run = await start(client, "keyframe", model_id="kling-3.0", prompt=f"{el['handle']} spins on a dark stage",
                      params={"duration": 5, "aspect_ratio": "16:9"})
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        frame_gen = await s.get(Generation, uuid.UUID(steps["keyframe"]["generation_id"]))
        shot_gen = await s.get(Generation, uuid.UUID(steps["shot"]["generation_id"]))
    assert frame_gen.model_id == "nano-banana-2" and frame_gen.mode == "edit"
    assert [m["asset_id"] for m in frame_gen.request["media"]] == [up["id"]]  # the character's picture
    assert frame_gen.request["params"]["aspect_ratio"] == "16:9"
    assert shot_gen.mode == "i2v" and shot_gen.request["media"][0]["asset_id"] == done["output"]["keyframe_asset_id"]


async def test_a_run_spends_only_what_was_charged_plus_what_is_still_running(client, in_memory_queue):
    from decimal import Decimal

    from studio.db.models import GenerationAttempt, PipelineRun, SpendLedger
    from studio.domain.enums import AttemptStatus, GenStatus, LedgerEntry
    from studio.pipelines.engine import run_spent

    run = await start(client, "compare", prompt="A kite over the dunes",
                      entries=[{"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}])
    async with session_scope() as s:
        row = await s.get(PipelineRun, uuid.UUID(run["id"]))
        user = row.user_id

        def gen(status: GenStatus, estimate: str) -> Generation:
            return Generation(user_id=user, group_id=row.id, model_id="kling-3.0", mode="t2v", output_type="video",
                              status=status, estimated_cost_usd=Decimal(estimate))

        rejected, billed, running = gen(GenStatus.failed, "0.40"), gen(GenStatus.failed, "0.50"), gen(GenStatus.queued, "0.30")
        s.add_all([rejected, billed, running])
        await s.flush()
        attempt = GenerationAttempt(generation_id=billed.id, attempt_no=1, route_id="kling-3.0@fal", provider="fal",
                                    status=AttemptStatus.failed)
        s.add(attempt)
        await s.flush()
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.charge, amount_usd=Decimal("0.10"),
                          generation_attempt_id=attempt.id))
        await s.flush()
        before = await run_spent(s, row)  # whatever the compare run itself made on the mock: free
        assert before == Decimal("0.40")  # the billed failure ($0.10) and the queued job's estimate ($0.30)
        # its Claude steps count too: a script written for this run, not one for another
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.charge, amount_usd=Decimal("0.07"),
                          pipeline_run_id=row.id, provider="anthropic"))
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.charge, amount_usd=Decimal("0.05"),
                          provider="anthropic"))
        await s.flush()
        assert await run_spent(s, row) == Decimal("0.47")
        await s.rollback()


async def test_a_claude_call_made_by_a_run_step_is_charged_to_the_run(monkeypatch):
    """While the engine runs a Claude step, structured() knows the run, so its charge counts against the run's cap."""
    from types import SimpleNamespace

    from studio.db.models import StepKind
    from studio.llm.client import CURRENT_RUN
    from studio.pipelines import engine

    seen = []

    async def op(session, run, step):
        seen.append(CURRENT_RUN.get())  # what structured() would book the charge to
        return {}

    monkeypatch.setitem(engine.LLM_OPS, "test_op", op)
    run = SimpleNamespace(id=uuid.uuid4())
    step = SimpleNamespace(kind=StepKind.llm, input={"op": "test_op"}, key="script", id=uuid.uuid4())
    await engine.execute_step(None, step, run)  # type: ignore[arg-type]
    assert seen == [run.id] and CURRENT_RUN.get() is None  # and only while the step runs


async def test_takes_still_rendering_when_a_run_fails_settle_into_their_steps(client, in_memory_queue):
    """A run fails on one step while others render; when those finish, their steps say so instead of "running",
    including runs left that way before (the reaper wakes them)."""
    from datetime import UTC, datetime

    from studio.config import get_settings
    from studio.db.models import PipelineRun, PipelineStep, RunStatus, StepKind, StepStatus
    from studio.domain.enums import AssetKind, AssetSource, AssetStatus, GenStatus
    from studio.worker.tasks.maintenance import reap

    user = get_settings().owner_user_id
    async with session_scope() as s:
        run = PipelineRun(user_id=user, recipe="sequence", title="halfway", status=RunStatus.failed,
                          error="Shot 2: moderation", input={"model_id": "kling-3.0", "shots": [{"prompt": "a"}]})
        s.add(run)
        await s.flush()
        gens, steps = [], []
        for i, status in enumerate([GenStatus.in_progress, GenStatus.in_progress]):
            g = Generation(user_id=user, model_id="kling-3.0", mode="t2v", output_type="video", status=status,
                           request={}, group_id=run.id)
            s.add(g)
            await s.flush()
            st = PipelineStep(run_id=run.id, key=f"shot[{i + 1:02d}]", kind=StepKind.generate, input={}, sort=i,
                              input_hash="x", status=StepStatus.running, generation_id=g.id)
            s.add(st)
            gens.append(g)
            steps.append(st)
        await s.commit()
        run_id, ids = run.id, [g.id for g in gens]

    async def finish(gen_id, status, error=None):
        async with session_scope() as s:
            g = await s.get(Generation, gen_id)
            g.status, g.error_message = status, error
            if status == GenStatus.succeeded:
                s.add(Asset(user_id=user, kind=AssetKind.video, status=AssetStatus.ready, source=AssetSource.generation,
                            mime="video/mp4", storage_key=f"test/{gen_id}.mp4", generation_id=gen_id, output_index=0,
                            duration_ms=5000))
            await s.commit()

    await finish(ids[0], GenStatus.succeeded)
    await worker.tasks["pipeline.advance"].func(run_id=str(run_id))  # the wake-up a finished take sends
    out = await get(client, str(run_id))
    assert out["status"] == "failed" and out["error"] == "Shot 2: moderation"  # still failed
    assert [st["status"] for st in out["steps"]] == ["succeeded", "running"]
    assert out["steps"][0]["output"]["duration_ms"] == 5000

    # a wake-up that never came: the reaper notices the finished take
    await finish(ids[1], GenStatus.failed, "the provider timed out")
    counts = await reap(datetime.now(UTC))
    assert counts["runs"] >= 1
    await drain(in_memory_queue)
    out = await get(client, str(run_id))
    assert [(st["status"], st["error"]) for st in out["steps"]] == [
        ("succeeded", None), ("failed", "the provider timed out")]
    assert (await reap(datetime.now(UTC)))["runs"] == 0  # nothing left to settle


async def test_a_deploy_never_redoes_finished_takes_but_an_edit_does(client, in_memory_queue, monkeypatch):
    """The plan of a waiting run changes because the registry or a catalog did (a deploy), not its input: its finished
    takes are kept. Editing the run's input still redoes them."""
    from studio.pipelines.recipes import get_recipe
    from studio.worker.tasks.pipeline import defer_advance

    run = await start(client, "compare", prompt="a paper boat", entries=[{"model_id": "kling-3.0"},
                                                                         {"model_id": "seedance-2.5"}])
    await drain(in_memory_queue)
    assert (await get(client, run["id"]))["status"] == "waiting"

    async def takes() -> int:
        async with session_scope() as s:
            return len((await s.execute(select(Generation.id).where(Generation.group_id == uuid.UUID(run["id"]))))
                       .scalars().all())

    before = await takes()
    recipe = get_recipe("compare")
    plan = type(recipe).plan

    def deployed(self, inp, settled, reg):  # what a changed model YAML does to every take's input
        specs = plan(self, inp, settled, reg)
        for sp in specs:
            if sp.kind.value == "generate":
                sp.input["params"] = {**sp.input.get("params", {}), "new_default": 1}
        return specs

    monkeypatch.setattr(type(recipe), "plan", deployed)
    await defer_advance(uuid.UUID(run["id"]))
    await drain(in_memory_queue)
    kept = await get(client, run["id"])
    assert await takes() == before and kept["status"] == "waiting"  # nothing paid for twice

    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {
        "prompt": "a paper boat at night", "entries": [{"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"}]}})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    assert await takes() == before + 2  # an edit to the run itself is redone

    # a revision that leaves two takes as they were, then another deploy: those two are still kept
    r = await client.post(f"/pipelines/{run['id']}/revise", json={"input": {
        "prompt": "a paper boat at night", "entries": [{"model_id": "kling-3.0"}, {"model_id": "seedance-2.5"},
                                                       {"model_id": "veo-3.1", "params": {"duration": 4}}]}})
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    assert await takes() == before + 3  # only the new entry's take

    def deployed_again(self, inp, settled, reg):
        specs = deployed(self, inp, settled, reg)
        for sp in specs:
            if sp.kind.value == "generate":
                sp.input["params"] = {**sp.input["params"], "another_default": 2}
        return specs

    monkeypatch.setattr(type(recipe), "plan", deployed_again)
    await defer_advance(uuid.UUID(run["id"]))
    await drain(in_memory_queue)
    assert await takes() == before + 3


async def test_runs_that_used_to_hang_now_end_or_carry_on(client, in_memory_queue, monkeypatch):
    """Ways a run sat at "running" forever: a take blocked by the budget cap, a local step whose job was lost in a
    restart, and a plan that can no longer be made (a model renamed in the registry)."""
    from studio.config import get_settings
    from studio.db.models import PipelineRun, PipelineStep, RunStatus, StepKind, StepStatus
    from studio.domain.enums import GenStatus
    from studio.pipelines.recipes import get_recipe
    from studio.registry.loader import get_registry

    user = get_settings().owner_user_id
    advance = worker.tasks["pipeline.advance"].func
    seq = get_recipe("sequence")
    first = seq.plan(seq.Input.model_validate({"model_id": "kling-3.0", "shots": [{"prompt": "a"}]}), {},
                     get_registry())[0]
    async with session_scope() as s:
        run = PipelineRun(user_id=user, recipe="sequence", title="capped", status=RunStatus.running,
                          input={"model_id": "kling-3.0", "shots": [{"prompt": "a"}]})
        s.add(run)
        await s.flush()
        g = Generation(user_id=user, model_id="kling-3.0", mode="t2v", output_type="video",
                       status=GenStatus.blocked_budget, status_reason="over the $20 daily cap", request={},
                       group_id=run.id)
        s.add(g)
        await s.flush()
        s.add(PipelineStep(run_id=run.id, key=first.key, kind=StepKind.generate, input=first.input,
                           input_hash=first.digest(), status=StepStatus.running, generation_id=g.id))
        lost = PipelineRun(user_id=user, recipe="sequence", title="restarted", status=RunStatus.running,
                           input={"model_id": "kling-3.0", "shots": [{"prompt": "a"}]})
        s.add(lost)
        await s.flush()
        join = PipelineStep(run_id=lost.id, key="join", kind=StepKind.local, input={"op": "concat"}, input_hash="x",
                            status=StepStatus.running)
        s.add(join)
        await s.commit()
        capped_id, gen_id, lost_id, join_id = run.id, g.id, lost.id, join.id

    await advance(run_id=str(capped_id))
    capped = await get(client, str(capped_id))
    assert capped["status"] == "failed" and "daily cap" in capped["error"]
    async with session_scope() as s:
        assert (await s.get(Generation, gen_id)).status == GenStatus.canceled  # no longer counted as pending

    await advance(run_id=str(lost_id))
    queued = [j for j in in_memory_queue.jobs.values()
              if j["task_name"] == "pipeline.step" and j["args"].get("step_id") == str(join_id)]
    assert queued, "the lost local step was not queued again"

    def broken(self, inp, settled, reg):
        raise KeyError("kling-3.0")

    monkeypatch.setattr(type(get_recipe("sequence")), "plan", broken)
    await advance(run_id=str(lost_id))
    gone = await get(client, str(lost_id))
    assert gone["status"] == "failed" and "couldn't plan" in gone["error"]


async def test_canceling_a_failed_run_stops_takes_still_rendering(client, in_memory_queue):
    from studio.config import get_settings
    from studio.db.models import PipelineRun, PipelineStep, RunStatus, StepKind, StepStatus
    from studio.domain.enums import GenStatus

    user = get_settings().owner_user_id
    async with session_scope() as s:
        run = PipelineRun(user_id=user, recipe="sequence", title="half failed", status=RunStatus.failed,
                          error="Shot 2: moderation", input={"model_id": "kling-3.0", "shots": [{"prompt": "a"}]})
        s.add(run)
        await s.flush()
        g = Generation(user_id=user, model_id="kling-3.0", mode="t2v", output_type="video",
                       status=GenStatus.queued, request={}, group_id=run.id)
        s.add(g)
        await s.flush()
        s.add(PipelineStep(run_id=run.id, key="shot[01]", kind=StepKind.generate, input={}, input_hash="x",
                           status=StepStatus.running, generation_id=g.id))
        await s.commit()
        run_id, gen_id = run.id, g.id
    r = await client.post(f"/pipelines/{run_id}/cancel")
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "failed" and out["error"] == "Shot 2: moderation"  # still says why it failed
    assert out["steps"][0]["status"] == "canceled"
    async with session_scope() as s:
        assert (await s.get(Generation, gen_id)).status == GenStatus.canceled  # the paid take was stopped


async def test_a_local_step_that_fails_leaves_nothing_behind(client, in_memory_queue, monkeypatch, tmp_path):
    """A split that fails on its third piece used to keep the first two as stray library assets (and their files)."""
    from types import SimpleNamespace

    from studio.config import get_settings
    from studio.db.models import PipelineRun, RunStatus, StepKind
    from studio.domain.derived import save_derived
    from studio.domain.enums import AssetKind, AssetSource
    from studio.pipelines import engine
    from studio.pipelines.ops import StepFailed

    made: list[str] = []

    async def split(session, run, step):
        for i in range(2):
            piece = tmp_path / f"piece{i}.wav"
            piece.write_bytes(b"RIFF")
            asset = await save_derived(session, run.user_id, piece, kind=AssetKind.archive,
                                       source=AssetSource.pipeline, title=f"piece {i}", parents=[])
            made.append(asset.storage_key)
        raise StepFailed("the third piece couldn't be cut")

    monkeypatch.setitem(engine.LOCAL_OPS, "test_split", split)
    async with session_scope() as s:
        run = PipelineRun(user_id=get_settings().owner_user_id, recipe="sequence", title="split",
                          status=RunStatus.running, input={})
        s.add(run)
        await s.flush()
        step = SimpleNamespace(kind=StepKind.local, input={"op": "test_split"}, key="parts", id=uuid.uuid4(),
                               status=None, error=None, finished_at=None)
        await engine.execute_step(s, step, run)
        await s.commit()
    assert step.status.value == "failed" and "third piece" in step.error and len(made) == 2
    async with session_scope() as s:
        left = (await s.execute(select(Asset).where(Asset.storage_key.in_(made)))).scalars().all()
    assert left == [] and not any(get_storage().path_for(k).exists() for k in made)


async def test_advancing_never_waits_behind_a_running_step(client, in_memory_queue):
    """A long ffmpeg step holds its row; an advance meanwhile (the reaper's, a finished take's) used to wait for it while
    holding the run, which kept Cancel, Revise and checkpoint answers waiting too."""
    from studio.config import get_settings
    from studio.db.models import PipelineRun, PipelineStep, RunStatus, StepKind, StepStatus
    from studio.worker.tasks.pipeline import advance

    async with session_scope() as s:
        run = PipelineRun(user_id=get_settings().owner_user_id, recipe="sequence", title="assembling",
                          status=RunStatus.running, input={"model_id": "kling-3.0", "shots": [{"prompt": "a"}]})
        s.add(run)
        await s.flush()
        step = PipelineStep(run_id=run.id, key="join", kind=StepKind.local, input={"op": "concat"}, input_hash="x",
                            status=StepStatus.running)
        s.add(step)
        await s.commit()
        run_id, step_id = run.id, step.id

    async with session_scope() as busy:  # the step's own job, mid-op
        await busy.get(PipelineStep, step_id, with_for_update=True)
        await asyncio.wait_for(advance(run_id), timeout=5)  # returns at once instead of waiting
        async with session_scope() as s:  # and the run isn't held: an API call can lock it right away
            await asyncio.wait_for(s.get(PipelineRun, run_id, with_for_update=True), timeout=5)
        await busy.rollback()
