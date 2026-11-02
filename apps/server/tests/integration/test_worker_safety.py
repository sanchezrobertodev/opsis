"""Worker paths that must never pay twice or lose a file, on the mock provider (free).

The paid side of a route timeout (parked while the job may still bill) is in test_paid_route_flow.py.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Asset, Generation, GenerationAttempt
from studio.db.session import session_scope
from studio.domain.enums import AssetKind, AssetSource, AssetStatus, GenStatus
from studio.providers.base import PollResult
from studio.providers.mock import MockAdapter
from studio.worker.tasks.generation import run_ingest, run_poll, run_submit


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def submitted(client, **body) -> tuple[uuid.UUID, uuid.UUID]:
    gen = (await client.post("/generations", json=body)).json()
    await run_submit(uuid.UUID(gen["id"]))
    async with session_scope() as s:
        attempt = (
            await s.execute(
                select(GenerationAttempt).where(GenerationAttempt.generation_id == uuid.UUID(gen["id"]))
            )
        ).scalar_one()
    return uuid.UUID(gen["id"]), attempt.id


async def test_a_free_take_past_its_routes_limit_still_fails_over(client, monkeypatch):
    """A free route can't bill, so a take that ran out of time moves on even when the cancel isn't confirmed."""

    async def running(self, route, job_id):
        return PollResult(status="running")

    async def unconfirmed(self, route, job_id):
        return False

    monkeypatch.setattr(MockAdapter, "poll", running)
    monkeypatch.setattr(MockAdapter, "cancel", unconfirmed)
    gen_id, attempt_id = await submitted(client, model_id="kling-3.0", mode="t2v", prompt="a calm sea")
    async with session_scope() as s:
        (await s.get(GenerationAttempt, attempt_id)).submitted_at = datetime.now(UTC) - timedelta(days=1)
    await run_poll(attempt_id)
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id)
    assert gen.status == GenStatus.queued and gen.routing["tried"] == ["kling-3.0@mock"]


async def test_two_ingests_of_one_take_never_share_a_folder(client, in_memory_queue, monkeypatch):
    """The reaper can start a second ingest beside a slow first one: each downloads into its own folder, one asset
    is kept and the take succeeds."""
    folders: list = []
    both = asyncio.Event()
    download = MockAdapter.download

    async def side_by_side(self, route, output, dest, workdir):
        folders.append(workdir)
        if len(folders) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 10)
        return await download(self, route, output, dest, workdir)

    monkeypatch.setattr(MockAdapter, "download", side_by_side)
    gen_id, attempt_id = await submitted(client, model_id="nano-banana-2", mode="t2i", prompt="a calm sea")
    await run_poll(attempt_id)
    await asyncio.gather(run_ingest(attempt_id), run_ingest(attempt_id))
    assert len(set(folders)) == 2
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id)
        assets = (await s.execute(select(Asset).where(Asset.generation_id == gen_id))).scalars().all()
    assert gen.status == GenStatus.succeeded and len(assets) == 1
    assert derived(in_memory_queue) == {str(assets[0].id)}


def derived(queue) -> set[str]:
    return {j["args"]["asset_id"] for j in queue.jobs.values() if j["task_name"] == "asset.derive"}


async def test_the_ingest_that_loses_the_race_still_derives_the_kept_asset(
    client, in_memory_queue, monkeypatch
):
    """Another ingest saved this output while this one downloaded it; this one may still be the one that settles
    the take, so it derives the other's asset (its pictures, its proxy)."""
    kept = uuid.uuid4()
    download = MockAdapter.download

    async def the_other_ingest_won(self, route, output, dest, workdir):
        async with session_scope() as s:
            gen = await s.get(Generation, gen_id)
            s.add(
                Asset(
                    id=kept,
                    user_id=gen.user_id,
                    kind=AssetKind.image,
                    status=AssetStatus.ready,
                    source=AssetSource.generation,
                    mime="image/png",
                    storage_key="kept.png",
                    generation_id=gen_id,
                    output_index=output.index,
                )
            )
        return await download(self, route, output, dest, workdir)

    monkeypatch.setattr(MockAdapter, "download", the_other_ingest_won)
    gen_id, attempt_id = await submitted(client, model_id="nano-banana-2", mode="t2i", prompt="a calm sea")
    await run_poll(attempt_id)
    await run_ingest(attempt_id)
    async with session_scope() as s:
        assert (await s.get(Generation, gen_id)).status == GenStatus.succeeded
    assert derived(in_memory_queue) == {str(kept)}
