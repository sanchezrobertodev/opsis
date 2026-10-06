"""End-to-end generation flow against a real Postgres, the real API and the real worker task code.

The queue is swapped for procrastinate's in-memory connector, and tests drive the task functions
directly, so every step is deterministic.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from studio.api.main import app as api
from studio.budget import guard
from studio.config import get_settings
from studio.db.base import uuid7
from studio.db.models import Asset, Generation, GenerationAttempt, JobEvent, SpendLedger
from studio.db.session import session_scope
from studio.domain.enums import AttemptStatus, GenStatus
from studio.storage.local import get_storage
from studio.worker.tasks.assets import run_derive
from studio.worker.tasks.generation import run_ingest, run_poll, run_submit
from studio.worker.tasks.maintenance import reap

OWNER = get_settings().owner_user_id


@pytest.fixture
async def client(db, in_memory_queue):
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def latest_attempt(gen_id: uuid.UUID) -> GenerationAttempt | None:
    async with session_scope() as s:
        return (await s.execute(select(GenerationAttempt).where(GenerationAttempt.generation_id == gen_id)
                                .order_by(GenerationAttempt.attempt_no.desc()).limit(1))).scalar_one_or_none()


async def status_of(gen_id: uuid.UUID) -> Generation:
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id)
        assert gen is not None
        return gen


async def drive(gen_id: uuid.UUID, max_submits: int = 4) -> Generation:
    """Run the worker steps the queue would run, until the generation settles."""
    for _ in range(max_submits):
        await run_submit(gen_id)
        gen = await status_of(gen_id)
        if gen.status != GenStatus.queued:
            break
    attempt = await latest_attempt(gen_id)
    for _ in range(5):
        gen = await status_of(gen_id)
        if gen.status != GenStatus.in_progress or attempt is None:
            break
        await run_poll(attempt.id)
    gen = await status_of(gen_id)
    if gen.status == GenStatus.ingesting and attempt is not None:
        await run_ingest(attempt.id)
    return await status_of(gen_id)


async def create(client, **body) -> dict:
    r = await client.post("/generations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_text_to_video_end_to_end(client, in_memory_queue):
    out = await create(client, model_id="veo-3.1", mode="t2v", prompt="a paper boat", params={"duration": 4})
    assert out["status"] == "queued"
    assert out["estimated_cost_usd"] == 0.0
    assert out["routing"]["live_reference"]["estimate_usd"] == pytest.approx(1.6)
    assert any(j["task_name"] == "gen.submit" for j in in_memory_queue.jobs.values())

    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.succeeded, gen.error_message

    detail = (await client.get(f"/generations/{out['id']}")).json()
    (asset,) = detail["assets"]
    assert asset["kind"] == "video" and asset["duration_ms"] == 4000 and asset["has_audio"] is True
    assert (asset["width"], asset["height"]) == (1280, 720)
    await run_derive(uuid.UUID(asset["id"]))
    asset = (await client.get(f"/assets/{asset['id']}")).json()
    assert asset["thumb_url"] and asset["poster_url"] and asset["filmstrip_url"]
    media = await client.get(asset["url"], headers={"Range": "bytes=0-99"})
    assert media.status_code == 206 and len(media.content) == 100

    async with session_scope() as s:
        types = (await s.execute(select(JobEvent.type).where(JobEvent.subject_id == gen.id)
                                 .order_by(JobEvent.id))).scalars().all()
    assert types[0] == "generation.queued" and types[-1] == "generation.succeeded"
    assert "generation.ingesting" in types


async def test_idempotency_key_returns_same_generation(client, in_memory_queue):
    body = {"model_id": "nano-banana-2", "mode": "t2i", "prompt": "idem"}
    a = await client.post("/generations", json=body, headers={"Idempotency-Key": "k-1"})
    b = await client.post("/generations", json=body, headers={"Idempotency-Key": "k-1"})
    assert a.json()["id"] == b.json()["id"]
    submits = [j for j in in_memory_queue.jobs.values()
               if j["task_name"] == "gen.submit" and j["args"]["generation_id"] == a.json()["id"]]
    assert len(submits) == 1


async def test_validation_errors_are_422(client):
    r = await client.post("/generations", json={"model_id": "veo-3.1", "mode": "t2v", "prompt": "x",
                                                "params": {"duration": 4, "resolution": "1080p"}})
    assert r.status_code == 422
    assert "8 seconds" in r.json()["errors"][0]
    r = await client.post("/generations", json={"model_id": "nope", "mode": "t2v", "prompt": "x"})
    assert r.status_code == 422


async def test_moderation_failure_is_terminal(client):
    out = await create(client, model_id="kling-3.0", mode="t2v", prompt="x [[mock:fail=moderation]]")
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.failed and gen.error_code.value == "moderation"


async def test_route_unavailable_fails_over_then_fails(client):
    out = await create(client, model_id="kling-3.0", mode="t2v", prompt="x [[mock:fail=route_unavailable]]")
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.failed and gen.error_code.value == "route_unavailable"
    assert gen.routing["tried"] == ["kling-3.0@mock"]


async def test_ambiguous_submit_goes_unknown_and_user_resolves(client, in_memory_queue):
    out = await create(client, model_id="kling-3.0", mode="t2v", prompt="x [[mock:ambiguous]]")
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.unknown
    # never resubmitted automatically
    assert sum(1 for j in in_memory_queue.jobs.values() if j["task_name"] == "gen.submit"
               and j["args"]["generation_id"] == out["id"]) == 1
    r = await client.post(f"/generations/{out['id']}/resolve-unknown")
    assert r.json()["status"] == "failed"


async def test_cancel_before_submit_stops_worker(client):
    out = await create(client, model_id="seedance-2.5", mode="t2v", prompt="cancel me")
    r = await client.post(f"/generations/{out['id']}/cancel")
    assert r.json()["status"] == "canceled"
    await run_submit(uuid.UUID(out["id"]))
    assert (await status_of(uuid.UUID(out["id"]))).status == GenStatus.canceled
    assert (await client.post(f"/generations/{out['id']}/cancel")).status_code == 409


async def test_upload_then_image_to_video_records_lineage(client):
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (200, 80, 40)).save(buf, "PNG")
    up = await client.post("/uploads", files={"file": ("frame.png", buf.getvalue(), "image/png")})
    assert up.status_code == 201, up.text
    image = up.json()
    assert image["kind"] == "image" and image["width"] == 640

    out = await create(client, model_id="veo-3.1", mode="i2v", prompt="animate",
                       media=[{"role": "start_image", "asset_id": image["id"]}], params={"duration": 4})
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.succeeded
    video_id = (await client.get(f"/generations/{out['id']}")).json()["assets"][0]["id"]
    lineage = (await client.get(f"/assets/{video_id}/lineage")).json()
    assert lineage["parents"][0]["parent_asset_id"] == image["id"]
    assert lineage["parents"][0]["relation"] == "start_image"


async def test_multi_image_output_and_library_listing(client):
    out = await create(client, model_id="nano-banana-2", mode="t2i", prompt="two", params={"num_images": 2})
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.succeeded
    page = (await client.get("/assets", params={"kind": "image", "source": "generation"})).json()
    ours = [a for a in page["items"] if a["generation_id"] == out["id"]]
    assert sorted(a["output_index"] for a in ours) == [0, 1]


async def test_budget_reserve_caps_and_settle(db):
    async with session_scope() as s:
        gen = Generation(id=uuid7(), user_id=OWNER, model_id="veo-3.1", mode="t2v", output_type="video",
                         status=GenStatus.submitting, request={}, routing={})
        s.add(gen)
        await s.flush()
        big = GenerationAttempt(id=uuid7(), generation_id=gen.id, attempt_no=1, route_id="r@x", provider="x")
        ok = GenerationAttempt(id=uuid7(), generation_id=gen.id, attempt_no=2, route_id="r@x", provider="x")
        s.add_all([big, ok])
        await s.flush()
        before = await guard.summary(s, OWNER)
        with pytest.raises(guard.BudgetBlocked):
            await guard.reserve(s, OWNER, big, before.daily_cap_usd + 1)
        await guard.reserve(s, OWNER, ok, Decimal("1.5"))
        await s.flush()
        mid = await guard.summary(s, OWNER)
        assert mid.reserved_usd - before.reserved_usd == Decimal("1.5")
        await guard.settle(s, OWNER, ok, Decimal("1.2"), estimated=False)
        await guard.settle(s, OWNER, ok, Decimal("1.2"), estimated=False)  # idempotent
        await s.flush()
        after = await guard.summary(s, OWNER)
        assert after.reserved_usd == before.reserved_usd
        assert after.today_usd - before.today_usd == Decimal("1.2")
        rows = (await s.execute(select(SpendLedger.entry_type).where(
            SpendLedger.generation_attempt_id == ok.id))).scalars().all()
        assert sorted(r.value for r in rows) == ["charge", "release", "reservation"]
        await s.rollback()


async def test_reaper_marks_stuck_submit_unknown(db, in_memory_queue):
    async with session_scope() as s:
        gen = Generation(id=uuid7(), user_id=OWNER, model_id="veo-3.1", mode="t2v", output_type="video",
                         status=GenStatus.submitting, request={}, routing={})
        s.add(gen)
        await s.flush()
        s.add(GenerationAttempt(id=uuid7(), generation_id=gen.id, attempt_no=1, route_id="veo-3.1@mock",
                                provider="mock", status=AttemptStatus.submitting,
                                created_at=datetime.now(UTC) - timedelta(minutes=5)))
    counts = await reap(datetime.now(UTC))
    assert counts["unknown"] >= 1
    assert (await status_of(gen.id)).status == GenStatus.unknown


async def test_files_land_under_media_root(client):
    out = await create(client, model_id="nano-banana-2", mode="t2i", prompt="disk")
    await drive(uuid.UUID(out["id"]))
    async with session_scope() as s:
        asset = (await s.execute(select(Asset).where(Asset.generation_id == uuid.UUID(out["id"])))).scalar_one()
    path = get_storage().path_for(asset.storage_key)
    assert path.is_file() and get_storage().root in path.parents
