"""Estimated charges corrected to the providers' bills (HTTP mocked, never a live call): fal per request, our RunPod
endpoint per settled hour, booked once, and counted wherever spend is summed."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select

from studio.api.main import app as api
from studio.budget.reconcile import reconcile
from studio.config import get_settings
from studio.db.models import Generation, GenerationAttempt, SpendLedger
from studio.db.session import session_scope
from studio.domain.enums import AttemptStatus, GenStatus, LedgerEntry


@pytest.fixture
async def client(db):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "fal_key", "fal-secret")
    monkeypatch.setattr(s, "runpod_api_key", "rp-secret")
    monkeypatch.setattr(s, "runpod_wan_core_endpoint", "ep-wan")


async def attempt(s, user, *, provider, route, job=None, usd=None, estimated=True, when):
    g = Generation(user_id=user, model_id=route.split("@")[0], mode="t2v", output_type="video",
                   status=GenStatus.succeeded, request={})
    s.add(g)
    await s.flush()
    a = GenerationAttempt(generation_id=g.id, attempt_no=1, route_id=route, provider=provider, provider_job_id=job,
                          status=AttemptStatus.succeeded, created_at=when)
    s.add(a)
    await s.flush()
    if usd is not None:
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.charge, amount_usd=Decimal(usd), provider=provider,
                          route_id=route, generation_attempt_id=a.id, estimated=estimated, created_at=when))
    return a


async def booked(s, **where) -> list[SpendLedger]:
    q = select(SpendLedger).where(SpendLedger.entry_type == LedgerEntry.adjustment)
    for k, v in where.items():
        q = q.where(getattr(SpendLedger, k) == v)
    return list((await s.execute(q.order_by(SpendLedger.created_at))).scalars().all())


@respx.mock
async def test_charges_are_corrected_to_the_bills_once(client, keys):
    user = get_settings().owner_user_id
    # a moment long past, so rows left by earlier runs of this test never share its hours
    now = (datetime.now(UTC) - timedelta(days=random.randint(40, 4000))).replace(minute=30, second=0, microsecond=0)
    hour = (now - timedelta(hours=5)).replace(minute=0)
    rid_cheap, rid_failed, rid_other = (str(uuid.uuid4()) for _ in range(3))

    async with session_scope() as s:
        cheap = await attempt(s, user, provider="fal", route="sam-3-video@fal", job=f"fal-ai/sam-3::{rid_cheap}",
                              usd="0.025", when=now - timedelta(hours=3))
        failed = await attempt(s, user, provider="fal", route="kling-3.0@fal", job=f"fal-ai/kling::{rid_failed}",
                               when=now - timedelta(hours=3))  # fal billed it; the studio charged nothing
        w1 = await attempt(s, user, provider="runpod_wan_core", route="wan-move@wan_core", job="ep-wan::j1",
                           usd="0.09", when=hour + timedelta(minutes=10))
        w2 = await attempt(s, user, provider="runpod_wan_core", route="wan-move@wan_core", job="ep-wan::j2",
                           usd="0.03", when=hour + timedelta(minutes=20))
        recent = await attempt(s, user, provider="runpod_wan_core", route="wan-move@wan_core", job="ep-wan::j3",
                               usd="0.05", when=now - timedelta(minutes=20))  # its hour hasn't settled yet
        ids = (cheap.id, failed.id, w1.id, w2.id, recent.id)

    fal = respx.get("https://api.fal.ai/v1/models/billing-events").respond(200, json={"next_cursor": None, "billing_events": [
        {"request_id": rid_cheap, "endpoint_id": "fal-ai/sam-3/video-rle", "cost_total": 0.005},
        {"request_id": rid_failed, "endpoint_id": "fal-ai/kling-video/v3", "cost_total": 0.42},
        {"request_id": rid_other, "endpoint_id": "fal-ai/flux", "cost_total": 0.5},  # another app on the same key
    ]})
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    runpod = respx.get("https://api.runpod.io/v2/billing/serverless").respond(200, json={"records": [
        {"startTime": iso(hour), "endTime": iso(hour + timedelta(hours=1)), "totalAmount": 0.18},
        {"startTime": iso(hour + timedelta(hours=1)), "endTime": iso(hour + timedelta(hours=2)), "totalAmount": 0.05},
    ]})

    async with session_scope() as s:
        results = {r.provider: r for r in await reconcile(s, days=1, now=now)}
    assert fal.calls.last.request.headers["authorization"] == "Key fal-secret"
    assert runpod.calls.last.request.url.params["serverlessId"] == "ep-wan"
    f, w = results["fal"], results["runpod_wan_core"]
    assert (f.status, f.checked, f.outside_usd) == ("ok", 2, 0.5)
    assert f.booked_usd == pytest.approx(-0.02 + 0.42)
    assert (w.status, w.checked) == ("ok", 2) and w.booked_usd == pytest.approx(0.06 + 0.05)
    assert "secret" not in repr(results)

    async with session_scope() as s:
        by_attempt = {r.generation_attempt_id: r for r in await booked(s) if r.generation_attempt_id in ids}
        assert by_attempt[ids[0]].amount_usd == Decimal("-0.020000")  # $0.025 estimate, $0.005 billed
        assert by_attempt[ids[1]].amount_usd == Decimal("0.420000")  # billed though nothing was charged
        # the hour's $0.06 over its two charges by size, dated like them
        assert (by_attempt[ids[2]].amount_usd, by_attempt[ids[3]].amount_usd) == (Decimal("0.045000"), Decimal("0.015000"))
        assert by_attempt[ids[2]].created_at == hour + timedelta(minutes=10)
        assert ids[4] not in by_attempt
        idle = [r for r in await booked(s, provider="runpod_wan_core", generation_attempt_id=None)
                if r.basis.get("bucket") == (hour + timedelta(hours=1)).isoformat()]
        assert [(r.amount_usd, r.route_id) for r in idle] == [(Decimal("0.050000"), "runpod_wan_core idle")]
        assert "idling" in idle[0].basis["label"]

    # again: nothing new to book
    async with session_scope() as s:
        again = {r.provider: r for r in await reconcile(s, days=1, now=now)}
    assert again["fal"].booked_usd == 0 and again["runpod_wan_core"].booked_usd == 0
    assert again["fal"].checked == 2


@respx.mock
async def test_a_key_that_cant_read_billing_books_nothing(client, keys):
    respx.get("https://api.fal.ai/v1/models/billing-events").respond(403, json={"error": {"type": "authorization_error"}})
    respx.get("https://api.runpod.io/v2/billing/serverless").mock(side_effect=httpx.ConnectTimeout("slow"))
    async with session_scope() as s:
        results = {r.provider: r for r in await reconcile(s, days=1)}
    assert results["fal"].status == "unavailable" and "admin" in results["fal"].detail
    assert results["runpod_wan_core"].status == "error"


async def test_corrections_count_as_spend_and_as_checked(client):
    user = get_settings().owner_user_id
    before = (await client.get("/budget/breakdown?period=today")).json()
    guard_before = (await client.get("/budget")).json()
    now = datetime.now(UTC)
    async with session_scope() as s:
        a = await attempt(s, user, provider="fal", route="veo-3.1@fal", job=f"fal-ai/veo::{uuid.uuid4()}", usd="1.00",
                          when=now)
        b = await attempt(s, user, provider="fal", route="veo-3.1@fal", job=f"fal-ai/veo::{uuid.uuid4()}", usd="0.50",
                          when=now)
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=Decimal("-0.20"), provider="fal",
                          route_id="veo-3.1@fal", generation_attempt_id=a.id, created_at=now,
                          basis={"source": "fal billing"}))
        assert b  # still an estimate
    after = (await client.get("/budget/breakdown?period=today")).json()
    assert after["total_usd"] - before["total_usd"] == pytest.approx(1.30)
    assert after["checked_usd"] - before["checked_usd"] == pytest.approx(0.80)  # $1.00 corrected to $0.80
    assert after["estimated_usd"] - before["estimated_usd"] == pytest.approx(0.50)
    veo = next(g for g in after["models"] if g["key"] == "veo-3.1")
    old = next((g for g in before["models"] if g["key"] == "veo-3.1"), {"count": 0})
    assert veo["count"] - old["count"] == 2  # a correction isn't another charge
    guard_after = (await client.get("/budget")).json()
    assert guard_after["today_usd"] - guard_before["today_usd"] == pytest.approx(1.30)


@respx.mock
async def test_the_api_runs_the_check(client, keys):
    respx.get("https://api.fal.ai/v1/models/billing-events").respond(200, json={"billing_events": []})
    respx.get("https://api.runpod.io/v2/billing/serverless").respond(200, json={"records": []})
    r = await client.post("/budget/reconcile?days=2")
    assert r.status_code == 200
    assert {x["provider"]: x["status"] for x in r.json()} == {"fal": "ok", "runpod_wan_core": "ok"}
