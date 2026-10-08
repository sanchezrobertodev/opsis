"""This month's spend split by studio feature and by model, from the ledger's charges."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from studio.api.main import app as api
from studio.config import get_settings
from studio.db.models import (
    Generation,
    GenerationAttempt,
    PipelineRun,
    PipelineStep,
    Project,
    SpendLedger,
    StepKind,
)
from studio.db.session import session_scope
from studio.domain.enums import AttemptStatus, GenStatus, LedgerEntry


@pytest.fixture
async def client(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def charge(s, user, gen: Generation | None, usd: str, *, estimated=False, when=None, provider="fal",
                 basis=None) -> None:
    attempt_id = None
    if gen is not None:
        s.add(gen)
        await s.flush()
        attempt = GenerationAttempt(generation_id=gen.id, attempt_no=1, route_id=f"{gen.model_id}@fal",
                                    provider=provider, status=AttemptStatus.succeeded)
        s.add(attempt)
        await s.flush()
        attempt_id = attempt.id
    entry = SpendLedger(user_id=user, entry_type=LedgerEntry.charge, amount_usd=Decimal(usd), provider=provider,
                        generation_attempt_id=attempt_id, estimated=estimated, basis=basis or {})
    if when is not None:
        entry.created_at = when
    s.add(entry)
    # a settled reservation is never spend (an open one would also hold the test user's daily cap)
    for kind, amount in ((LedgerEntry.reservation, "5"), (LedgerEntry.release, "-5")):
        s.add(SpendLedger(user_id=user, entry_type=kind, amount_usd=Decimal(amount), provider=provider))


async def test_spend_by_feature_and_model(client):
    user = get_settings().owner_user_id

    def gen(model, mode, **kw) -> Generation:
        return Generation(user_id=user, model_id=model, mode=mode, output_type="video", status=GenStatus.succeeded,
                          request={}, **kw)

    # other tests' charges may already be in the month: compare against what was there before
    before = (await client.get("/budget/breakdown")).json()
    before_all = (await client.get("/budget/breakdown?period=all")).json()

    def delta(now: dict, then: dict, field: str) -> dict:
        old = {g["key"]: g for g in then[field]}
        out = {}
        for g in now[field]:
            o = old.get(g["key"], {"usd": 0, "count": 0, "estimated_usd": 0})
            if g["count"] != o["count"]:
                out[g["key"]] = (round(g["usd"] - o["usd"], 6), g["count"] - o["count"],
                                 round(g["estimated_usd"] - o["estimated_usd"], 6), g["label"])
        return out

    async with session_scope() as s:
        run = PipelineRun(user_id=user, recipe="compare", title="kites", input={})
        film = Project(user_id=user, kind="cinema", title="Harbour", document={})
        s.add_all([run, film])
        await s.flush()
        step = PipelineStep(run_id=run.id, key="entry[1]", kind=StepKind.generate, input={}, input_hash="x")
        s.add(step)
        await s.flush()
        await charge(s, user, gen("veo-3.1", "t2v", pipeline_step_id=step.id), "1.60", estimated=True)
        await charge(s, user, gen("sync-lipsync-3", "lipsync", project_id=film.id, shot_key="sone"), "0.40")
        await charge(s, user, gen("sync-lipsync-3", "lipsync"), "0.40")
        await charge(s, user, None, "0.03", provider="anthropic", basis={"purpose": "cinema.plan",
                                                                         "model": "claude-opus-5-5"})
        await charge(s, user, gen("veo-3.1", "t2v"), "9.00", when=datetime.now(UTC) - timedelta(days=400))
        await s.commit()

    month = (await client.get("/budget/breakdown")).json()
    assert month["period"] == "month"
    assert month["total_usd"] - before["total_usd"] == pytest.approx(2.43)
    features = delta(month, before, "features")
    assert {k: v[:3] for k, v in features.items()} == {
        "create_video": (1.6, 1, 1.6), "cinema": (0.4, 1, 0), "lipsync": (0.4, 1, 0), "claude": (0.03, 1, 0)}
    assert features["create_video"][3] == "Create video" and features["cinema"][3] == "Cinema studio"
    usds = [g["usd"] for g in month["features"]]
    assert usds == sorted(usds, reverse=True)  # most spent first
    models = delta(month, before, "models")
    assert models["veo-3.1"] == (1.6, 1, 1.6, "Veo 3.1") and models["sync-lipsync-3"][:2] == (0.8, 2)
    assert models["claude-opus-5-5"][3] == "Claude Opus 5.5" and models["claude-opus-5-5"][0] == 0.03

    ever = (await client.get("/budget/breakdown?period=all")).json()
    assert ever["total_usd"] - before_all["total_usd"] == pytest.approx(11.43)
    assert (await client.get("/budget/breakdown?period=year")).status_code == 422
