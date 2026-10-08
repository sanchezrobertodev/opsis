"""Estimated charges corrected to the providers' bills (HTTP mocked, never a live call): fal per request, our RunPod
endpoint per settled hour shared by the jobs that ran in it, booked once even by overlapping runs, and counted
wherever spend is summed."""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def long_ago() -> datetime:
    """A moment long past, so rows left by earlier runs of these tests never share its hours."""
    return (datetime.now(UTC) - timedelta(days=random.randint(40, 4000))).replace(minute=30, second=0, microsecond=0)


async def attempt(s, user, *, provider, route, job=None, usd=None, estimated=True, when, ran=None):
    """`ran`: (sent, finished) for a job on our RunPod endpoint; it's charged when it finishes."""
    g = Generation(user_id=user, model_id=route.split("@")[0], mode="t2v", output_type="video",
                   status=GenStatus.succeeded, request={})
    s.add(g)
    await s.flush()
    a = GenerationAttempt(generation_id=g.id, attempt_no=1, route_id=route, provider=provider, provider_job_id=job,
                          status=AttemptStatus.succeeded, created_at=when,
                          submitted_at=ran[0] if ran else None, finished_at=ran[1] if ran else None)
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
    now = long_ago()
    hour = (now - timedelta(hours=5)).replace(minute=0)

    def m(x: int) -> datetime:
        return hour + timedelta(minutes=x)

    rid_cheap, rid_failed, rid_other = (str(uuid.uuid4()) for _ in range(3))
    wan = {"provider": "runpod_wan_core", "route": "wan-move@wan_core"}
    async with session_scope() as s:
        cheap = await attempt(s, user, provider="fal", route="sam-3-video@fal", job=f"fal-ai/sam-3::{rid_cheap}",
                              usd="0.025", when=now - timedelta(hours=3))
        failed = await attempt(s, user, provider="fal", route="kling-3.0@fal", job=f"fal-ai/kling::{rid_failed}",
                               when=now - timedelta(hours=3))  # fal billed it; the studio charged nothing
        # with the 60 s idle tail: w1 runs 6 min and w2 6 min in the first hour; w3 3 min there and 4 in the next
        w1 = await attempt(s, user, **wan, job="ep-wan::j1", usd="0.09", when=m(10), ran=(m(5), m(10)))
        w2 = await attempt(s, user, **wan, job="ep-wan::j2", usd="0.03", when=m(20), ran=(m(15), m(20)))
        w3 = await attempt(s, user, **wan, job="ep-wan::j3", usd="0.10", when=m(63), ran=(m(57), m(63)))
        recent = await attempt(s, user, **wan, job="ep-wan::j4", usd="0.05", when=now - timedelta(minutes=20),
                               ran=(now - timedelta(minutes=25), now - timedelta(minutes=20)))  # not settled yet

    fal = respx.get("https://api.fal.ai/v1/models/billing-events").respond(200, json={"next_cursor": None, "billing_events": [
        {"request_id": rid_cheap, "endpoint_id": "fal-ai/sam-3/video-rle", "cost_total": 0.005},
        {"request_id": rid_failed, "endpoint_id": "fal-ai/kling-video/v3", "cost_total": 0.42},
        {"request_id": rid_other, "endpoint_id": "fal-ai/flux", "cost_total": 0.5},  # another app on the same key
    ]})
    runpod = respx.get("https://api.runpod.io/v2/billing/serverless").respond(200, json={"records": [
        {"startTime": iso(m(0)), "endTime": iso(m(60)), "totalAmount": 0.18},
        {"startTime": iso(m(60)), "endTime": iso(m(120)), "totalAmount": 0.08},
        {"startTime": iso(m(120)), "endTime": iso(m(180)), "totalAmount": 0.05},  # nothing ran: idle
    ]})

    async with session_scope() as s:
        results = {r.provider: r for r in await reconcile(s, days=1, now=now)}
    assert fal.calls.last.request.headers["authorization"] == "Key fal-secret"
    assert runpod.calls.last.request.url.params["serverlessId"] == "ep-wan"
    f, w = results["fal"], results["runpod_wan_core"]
    assert (f.status, f.checked, f.outside_usd) == ("ok", 2, 0.5)
    assert f.booked_usd == pytest.approx(-0.02 + 0.42)
    # hour 1's $0.18 over 15 job-minutes: w1 and w2 $0.072 each, w3 $0.036; hour 2's $0.08 all w3's; hour 3 idle
    assert (w.status, w.checked) == ("ok", 3)
    assert w.booked_usd == pytest.approx((0.072 - 0.09) + (0.072 - 0.03) + (0.116 - 0.10) + 0.05)
    assert "secret" not in repr(results)

    async with session_scope() as s:
        by_attempt = {r.generation_attempt_id: r for r in await booked(s)}
        assert by_attempt[cheap.id].amount_usd == Decimal("-0.020000")  # $0.025 estimate, $0.005 billed
        assert by_attempt[failed.id].amount_usd == Decimal("0.420000")  # billed though nothing was charged
        # each job's share of the hours it ran in, minus what it was charged, dated like its charge
        assert by_attempt[w1.id].amount_usd == Decimal("-0.018000") and by_attempt[w1.id].created_at == m(10)
        assert by_attempt[w2.id].amount_usd == Decimal("0.042000")
        assert by_attempt[w3.id].amount_usd == Decimal("0.016000")  # across the hour boundary: $0.036 + $0.08
        assert by_attempt[w3.id].basis["hours"] == [m(0).isoformat(), m(60).isoformat()]
        assert recent.id not in by_attempt
        idle = [r for r in await booked(s, provider="runpod_wan_core", generation_attempt_id=None)
                if r.basis.get("bucket") == m(120).isoformat()]
        assert [(r.amount_usd, r.route_id) for r in idle] == [(Decimal("0.050000"), "runpod_wan_core idle")]
        assert "idling" in idle[0].basis["label"]

    # again: nothing new to book
    async with session_scope() as s:
        again = {r.provider: r for r in await reconcile(s, days=1, now=now)}
    assert again["fal"].booked_usd == 0 and again["runpod_wan_core"].booked_usd == 0
    assert again["fal"].checked == 2


@respx.mock
async def test_overlapping_checks_book_an_idle_hour_once(client, keys):
    """The hourly worker and the Settings button can run together; the second waits for the first and finds it done."""
    user = get_settings().owner_user_id
    now = long_ago()
    hour = (now - timedelta(hours=5)).replace(minute=0)
    async with session_scope() as s:  # someone used the endpoint before the idle hour
        await attempt(s, user, provider="runpod_wan_core", route="wan-move@wan_core", job="ep-wan::x", usd="0.05",
                      when=hour - timedelta(minutes=50), ran=(hour - timedelta(minutes=55), hour - timedelta(minutes=50)))
    respx.get("https://api.fal.ai/v1/models/billing-events").respond(200, json={"billing_events": []})
    respx.get("https://api.runpod.io/v2/billing/serverless").respond(200, json={"records": [
        {"startTime": iso(hour), "endTime": iso(hour + timedelta(hours=1)), "totalAmount": 0.07}]})

    async def run():
        async with session_scope() as s:
            return {r.provider: r for r in await reconcile(s, days=1, now=now)}

    first, second = await asyncio.gather(run(), run())
    assert sorted([first["runpod_wan_core"].booked_usd, second["runpod_wan_core"].booked_usd]) == [0, pytest.approx(0.07)]
    async with session_scope() as s:
        rows = [r for r in await booked(s, provider="runpod_wan_core", generation_attempt_id=None)
                if r.basis.get("bucket") == hour.isoformat()]
        assert len(rows) == 1
    # and the database refuses a second one outright
    with pytest.raises(IntegrityError):
        async with session_scope() as s:
            s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=Decimal("0.07"),
                              provider="runpod_wan_core", basis={"bucket": hour.isoformat()}))


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
    history_before = {p["provider"]: p for p in (await client.get("/budget/history")).json()["by_provider"]}
    now = datetime.now(UTC)
    async with session_scope() as s:
        a = await attempt(s, user, provider="fal", route="veo-3.1@fal", job=f"fal-ai/veo::{uuid.uuid4()}", usd="1.00",
                          when=now)
        await attempt(s, user, provider="fal", route="veo-3.1@fal", job=f"fal-ai/veo::{uuid.uuid4()}", usd="0.50",
                      when=now)  # still an estimate
        s.add(SpendLedger(user_id=user, entry_type=LedgerEntry.adjustment, amount_usd=Decimal("-0.20"), provider="fal",
                          route_id="veo-3.1@fal", generation_attempt_id=a.id, created_at=now,
                          basis={"source": "fal billing"}))
    after = (await client.get("/budget/breakdown?period=today")).json()
    assert after["total_usd"] - before["total_usd"] == pytest.approx(1.30)
    assert after["checked_usd"] - before["checked_usd"] == pytest.approx(0.80)  # $1.00 corrected to $0.80
    assert after["estimated_usd"] - before["estimated_usd"] == pytest.approx(0.50)
    veo = next(g for g in after["models"] if g["key"] == "veo-3.1")
    old = next((g for g in before["models"] if g["key"] == "veo-3.1"), {"count": 0})
    assert veo["count"] - old["count"] == 2  # a correction isn't another charge
    guard_after = (await client.get("/budget")).json()
    assert guard_after["today_usd"] - guard_before["today_usd"] == pytest.approx(1.30)
    fal_now = {p["provider"]: p for p in (await client.get("/budget/history")).json()["by_provider"]}["fal"]
    fal_then = history_before.get("fal", {"count": 0, "usd": 0})
    assert (fal_now["count"] - fal_then["count"], round(fal_now["usd"] - fal_then["usd"], 6)) == (2, 1.30)


@respx.mock
async def test_the_api_runs_the_check(client, keys):
    respx.get("https://api.fal.ai/v1/models/billing-events").respond(200, json={"billing_events": []})
    respx.get("https://api.runpod.io/v2/billing/serverless").respond(200, json={"records": []})
    r = await client.post("/budget/reconcile?days=2")
    assert r.status_code == 200
    assert {x["provider"]: x["status"] for x in r.json()} == {"fal": "ok", "runpod_wan_core": "ok"}
