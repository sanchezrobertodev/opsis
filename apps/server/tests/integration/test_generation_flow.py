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
    async def sending_since(minutes: int) -> uuid.UUID:
        async with session_scope() as s:
            gen = Generation(id=uuid7(), user_id=OWNER, model_id="veo-3.1", mode="t2v", output_type="video",
                             status=GenStatus.submitting, request={}, routing={})
            s.add(gen)
            await s.flush()
            s.add(GenerationAttempt(id=uuid7(), generation_id=gen.id, attempt_no=1, route_id="veo-3.1@mock",
                                    provider="mock", status=AttemptStatus.submitting,
                                    created_at=datetime.now(UTC) - timedelta(minutes=minutes)))
        return gen.id

    uploading, crashed = await sending_since(5), await sending_since(40)
    await reap(datetime.now(UTC))
    # a slow upload is still a submit in progress; only one past every limit is a worker that stopped
    assert (await status_of(uploading)).status == GenStatus.submitting
    assert (await status_of(crashed)).status == GenStatus.unknown


async def test_files_land_under_media_root(client):
    out = await create(client, model_id="nano-banana-2", mode="t2i", prompt="disk")
    await drive(uuid.UUID(out["id"]))
    async with session_scope() as s:
        asset = (await s.execute(select(Asset).where(Asset.generation_id == uuid.UUID(out["id"])))).scalar_one()
    path = get_storage().path_for(asset.storage_key)
    assert path.is_file() and get_storage().root in path.parents


async def test_director_compiles_into_the_provider_prompt(client):
    out = await create(client, model_id="kling-3.0", mode="t2v", prompt="a lighthouse at night",
                       direction={"camera_moves": [{"id": "crane_up"}], "lighting": "blue_hour"})
    assert out["request"]["prompt"] == "a lighthouse at night"
    assert out["request"]["compiled_prompt"].startswith("a lighthouse at night, camera: crane shot")
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.succeeded
    asset = (await client.get(f"/generations/{out['id']}")).json()["assets"][0]
    assert asset["metadata"]["prompt"] == "a lighthouse at night"
    assert "blue-hour" in asset["metadata"]["compiled_prompt"]
    assert asset["metadata"]["direction"]["lighting"] == "blue_hour"


async def test_bad_direction_is_a_validation_error(client):
    r = await client.post("/generations", json={"model_id": "kling-3.0", "mode": "t2v", "prompt": "x",
                                                "direction": {"camera_moves": [{"id": "a"}] * 4}})
    assert r.status_code == 422


def test_a_reported_cost_is_read_the_same_way_everywhere():
    from decimal import Decimal

    from studio.worker.tasks.generation import _reported_cost

    assert _reported_cost({"cost_usd": 0.42}) == Decimal("0.42")
    assert _reported_cost({"cost_usd": 0}) is None  # a failed job saying $0 most likely didn't say
    assert _reported_cost({"cost_usd": 0}, free_ok=True) == Decimal("0")  # a finished one may have been free
    for bad in ("0.4", None, True, -1, {"usd": 1}):  # not a number: the estimate stands, nothing crashes
        assert _reported_cost({"cost_usd": bad}, free_ok=True) is None


async def test_deleting_a_take_removes_its_files_and_keeps_what_others_use(client):
    out = await create(client, model_id="nano-banana-2", mode="t2i", prompt="tidy up", params={"num_images": 2})
    gen_id = uuid.UUID(out["id"])
    assert (await client.delete(f"/generations/{out['id']}")).status_code == 409  # still queued
    await drive(gen_id)
    async with session_scope() as s:
        assets = (await s.execute(select(Asset).where(Asset.generation_id == gen_id)
                                  .order_by(Asset.output_index))).scalars().all()
        ledger = select(SpendLedger.id).join(GenerationAttempt, SpendLedger.generation_attempt_id == GenerationAttempt.id
                                             ).where(GenerationAttempt.generation_id == gen_id)
        spent = len((await s.execute(ledger)).all())
    used, loose = assets
    r = await client.post("/elements", json={"kind": "character", "name": "Mara Vale",
                                             "images": [{"asset_id": str(used.id), "view": "front"}]})
    assert r.status_code == 201, r.text
    assert (await client.delete(f"/assets/{used.id}")).status_code == 409

    r = await client.delete(f"/generations/{out['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == {"files_removed": 1, "kept": ["the character Mara Vale"]}
    storage = get_storage()
    assert not storage.path_for(loose.storage_key).exists()
    assert storage.path_for(used.storage_key).is_file()
    detail = await client.get(f"/generations/{out['id']}")  # still readable: the kept output points back at it
    assert detail.status_code == 200 and [a["id"] for a in detail.json()["assets"]] == [str(used.id)]
    listed = (await client.get("/generations")).json()
    assert out["id"] not in [g["id"] for g in listed["items"]]
    async with session_scope() as s:
        assert len((await s.execute(ledger)).all()) == spent  # what it cost stays on the books


async def test_a_take_whose_last_file_is_deleted_leaves_history(client):
    out = await create(client, model_id="nano-banana-2", mode="t2i", prompt="two of them", params={"num_images": 2})
    await drive(uuid.UUID(out["id"]))
    async with session_scope() as s:
        first, second = (await s.execute(select(Asset.id).where(Asset.generation_id == uuid.UUID(out["id"]))
                                         .order_by(Asset.output_index))).scalars().all()

    def listed(page) -> bool:
        return out["id"] in [g["id"] for g in page.json()["items"]]

    assert (await client.delete(f"/assets/{first}")).status_code == 204
    assert listed(await client.get("/generations"))  # one file left
    assert (await client.delete(f"/assets/{second}")).status_code == 204
    assert not listed(await client.get("/generations"))


async def test_files_a_take_still_rendering_needs_are_kept(client):
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (90, 60, 30)).save(buf, "PNG")
    image = (await client.post("/uploads", files={"file": ("frame.png", buf.getvalue(), "image/png")})).json()
    out = await create(client, model_id="veo-3.1", mode="i2v", prompt="animate",
                       media=[{"role": "start_image", "asset_id": image["id"]}], params={"duration": 4})
    r = await client.delete(f"/assets/{image['id']}")
    assert r.status_code == 409 and "a take still rendering" in r.json()["detail"].lower()
    gen = await drive(uuid.UUID(out["id"]))
    assert gen.status == GenStatus.succeeded, gen.error_message
    assert (await client.delete(f"/assets/{image['id']}")).status_code == 204


async def _upload_picture(client) -> uuid.UUID:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 160, 90)).save(buf, "PNG")
    return uuid.UUID((await client.post("/uploads", files={"file": ("pic.png", buf.getvalue(), "image/png")})).json()["id"])


async def test_files_a_delete_could_not_remove_are_retried_by_the_reaper(client, monkeypatch):
    from studio.domain.removal import PURGE

    aid = await _upload_picture(client)
    storage = get_storage()
    async with session_scope() as s:
        path = storage.path_for((await s.get(Asset, aid)).storage_key)
    with monkeypatch.context() as m:
        m.setattr(type(storage), "delete_prefix", lambda self, prefix: None)  # a file held open: nothing goes
        assert (await client.delete(f"/assets/{aid}")).status_code == 204
    async with session_scope() as s:
        assert (await s.get(Asset, aid)).metadata_.get(PURGE) is True
    assert path.is_file()
    assert (await reap(datetime.now(UTC)))["files_left"] == 0
    assert not path.exists()
    async with session_scope() as s:
        assert PURGE not in (await s.get(Asset, aid)).metadata_


async def test_a_delete_waits_for_a_save_that_starts_using_the_asset(client):
    import asyncio
    from types import SimpleNamespace

    from studio.api.routers.projects import _available
    from studio.db.models import Project

    aid = await _upload_picture(client)
    project = (await client.post("/projects", json={"title": "Race", "document": {}})).json()
    async with session_scope() as s:  # a save, part way: it has checked the asset and not committed yet
        asset = await s.get(Asset, aid)
        assert await _available(s, SimpleNamespace(id=asset.user_id), {aid}) == {aid}
        deleting = asyncio.create_task(client.delete(f"/assets/{aid}"))
        await asyncio.sleep(0.5)
        assert not deleting.done()
        (await s.get(Project, uuid.UUID(project["id"]))).document = {"picked": str(aid)}
    r = await deleting
    assert r.status_code == 409 and "the film race" in r.json()["detail"].lower()


async def test_show_in_folder_only_for_a_browser_on_this_machine(client, monkeypatch):
    from studio.media import reveal

    shown: list = []
    monkeypatch.setattr(reveal, "in_container", lambda: False)
    monkeypatch.setattr(reveal, "opener", lambda path: ["file-manager", str(path)])
    monkeypatch.setattr(reveal, "reveal", shown.append)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 120, 200)).save(buf, "PNG")
    image = (await client.post("/uploads", files={"file": ("pic.png", buf.getvalue(), "image/png")})).json()

    assert (await client.get("/health")).json()["reveal_files"] is True
    assert (await client.post(f"/assets/{image['id']}/reveal")).status_code == 204
    async with session_scope() as s:
        asset = await s.get(Asset, uuid.UUID(image["id"]))
    assert shown == [get_storage().path_for(asset.storage_key)]

    proxied = {"X-Forwarded-For": "100.64.0.7"}  # e.g. a tailnet proxy in front of the API
    assert (await client.get("/health", headers=proxied)).json()["reveal_files"] is False
    assert (await client.post(f"/assets/{image['id']}/reveal", headers=proxied)).status_code == 403
    monkeypatch.setattr(reveal, "in_container", lambda: True)
    assert (await client.post(f"/assets/{image['id']}/reveal")).status_code == 403
    assert shown == [get_storage().path_for(asset.storage_key)]
