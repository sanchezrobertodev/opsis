"""A take the provider refuses on content grounds gets one more try with a prompt Claude rewords."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Generation, PipelineStep
from studio.db.session import session_scope
from studio.pipelines.llm_ops import Reworded
from studio.pipelines.ops import StepFailed

REFUSED = "[[mock:fail=moderation]]"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def _sequence(client, queue, first_prompt: str) -> tuple[dict, list[Generation], PipelineStep]:
    from tests.integration.test_pipelines import drain, get, start  # importable once the suite is collected

    run = await start(client, "sequence", model_id="veo-3.1", shots=[
        {"prompt": first_prompt, "duration": 4}, {"prompt": "the lamp turns on", "duration": 4}])
    await drain(queue)
    done = await get(client, run["id"])
    async with session_scope() as s:
        step = (await s.execute(select(PipelineStep).where(
            PipelineStep.run_id == uuid.UUID(run["id"]), PipelineStep.key == "shot[01]"))).scalar_one()
        gens = (await s.execute(select(Generation).where(Generation.pipeline_step_id == step.id)
                                .order_by(Generation.created_at))).scalars().all()
    return done, list(gens), step


async def test_a_refused_take_is_reworded_once_and_the_run_goes_on(client, in_memory_queue):
    done, gens, step = await _sequence(client, in_memory_queue, f"a lighthouse keeper falls on the rocks {REFUSED}")
    assert done["status"] == "succeeded", done["error"]
    assert [g.status.value for g in gens] == ["failed", "succeeded"]
    assert gens[0].error_code.value == "moderation"
    assert gens[1].request["prompt"] == "a lighthouse keeper falls on the rocks Gently worded."
    assert step.input["_reworded"]["from"].endswith(REFUSED) and "_reword" not in step.input


async def test_a_reworded_take_refused_again_fails_as_before(client, in_memory_queue, monkeypatch):
    async def still_refused(session, run, prompt, reason):
        return Reworded(prompt=f"a softer fall {REFUSED}", changed="softer")

    monkeypatch.setattr("studio.pipelines.engine.reword", still_refused)
    done, gens, step = await _sequence(client, in_memory_queue, f"a fall {REFUSED}")
    assert done["status"] == "failed" and [g.status.value for g in gens] == ["failed", "failed"]
    assert step.input["prompt"] == f"a softer fall {REFUSED}"  # one rewording, never a second


async def test_a_prompt_claude_wont_reword_fails_with_both_reasons(client, in_memory_queue, monkeypatch):
    async def kept(session, run, prompt, reason):
        raise StepFailed("Claude kept the prompt as it was: it shouldn't be made")

    monkeypatch.setattr("studio.pipelines.engine.reword", kept)
    done, gens, step = await _sequence(client, in_memory_queue, f"a fall {REFUSED}")
    assert done["status"] == "failed" and len(gens) == 1
    assert "a reworded try wasn't possible: Claude kept the prompt" in done["error"]
    assert step.input["_reworded"]["failed"].startswith("Claude kept")
