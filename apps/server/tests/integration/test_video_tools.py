"""Local video tools on real ffmpeg, directly and as a normal take through the job pipeline."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from studio.api.main import app as api
from studio.db.models import Generation, GenerationAttempt
from studio.db.session import session_scope
from studio.domain.enums import GenStatus
from studio.media import tools
from studio.media.ffmpeg import probe
from studio.worker.tasks.generation import run_ingest, run_poll, run_submit

CLIP = Path(__file__).resolve().parents[4] / "fixtures" / "videos" / "clip_3s.mp4"


@pytest.fixture
def clip(tmp_path) -> Path:
    dest = tmp_path / "in.mp4"
    shutil.copy(CLIP, dest)
    return dest


async def test_reframe_keeps_pixel_count_and_hits_the_aspect(clip, tmp_path):
    src = await probe(clip)
    for fit in ("crop", "blur_fill", "pad"):
        out = tmp_path / f"{fit}.mp4"
        await tools.reframe(clip, out, "9:16", fit)
        info = await probe(out)
        assert info.width / info.height == pytest.approx(9 / 16, abs=0.01)
        assert info.width * info.height == pytest.approx(src.width * src.height, rel=0.05)
        assert info.duration_ms == pytest.approx(src.duration_ms, abs=150)


async def test_trim_retime_reverse_and_loop_change_length_as_expected(clip, tmp_path):
    src = (await probe(clip)).duration_ms / 1000
    cases = [
        (tools.trim(clip, tmp_path / "t.mp4", 0.5, 2.0), "t.mp4", 1.5),
        (tools.retime(clip, tmp_path / "f.mp4", 2.0), "f.mp4", src / 2),
        (tools.retime(clip, tmp_path / "s.mp4", 0.5), "s.mp4", src * 2),
        (tools.reverse(clip, tmp_path / "r.mp4"), "r.mp4", src),
        (tools.loop(clip, tmp_path / "l.mp4", 3), "l.mp4", src * 3),
        (tools.loop(clip, tmp_path / "b.mp4", 1, boomerang=True), "b.mp4", src * 2),
    ]
    for job, name, seconds in cases:
        await job
        assert (await probe(tmp_path / name)).duration_ms / 1000 == pytest.approx(seconds, abs=0.25), name
    with pytest.raises(ValueError, match="empty"):
        await tools.trim(clip, tmp_path / "x.mp4", 2.0, 2.0)
    with pytest.raises(ValueError, match="between"):
        await tools.retime(clip, tmp_path / "x.mp4", 10)


async def test_interpolate_upscale_and_stabilize(clip, tmp_path):
    src = await probe(clip)
    await tools.interpolate(clip, tmp_path / "i.mp4", 60, "blend")
    assert (await probe(tmp_path / "i.mp4")).fps == pytest.approx(60, abs=0.5)
    await tools.upscale(clip, tmp_path / "u.mp4", 2)
    up = await probe(tmp_path / "u.mp4")
    assert (up.width, up.height) == (src.width * 2, src.height * 2)
    await tools.stabilize(clip, tmp_path / "st.mp4")
    assert (await probe(tmp_path / "st.mp4")).duration_ms == pytest.approx(src.duration_ms, abs=150)


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def status_of(gen_id: uuid.UUID) -> Generation:
    async with session_scope() as s:
        gen = await s.get(Generation, gen_id)
        assert gen is not None
        return gen


async def latest_attempt(gen_id: uuid.UUID) -> GenerationAttempt | None:
    async with session_scope() as s:
        return (await s.execute(select(GenerationAttempt).where(GenerationAttempt.generation_id == gen_id)
                                .order_by(GenerationAttempt.attempt_no.desc()).limit(1))).scalar_one_or_none()


async def drive_local(gen_id: uuid.UUID, timeout: float = 60):
    await run_submit(gen_id)
    attempt = await latest_attempt(gen_id)
    assert attempt is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        gen = await status_of(gen_id)
        if gen.status == GenStatus.ingesting:
            await run_ingest(attempt.id)
            break
        if gen.status.terminal:
            break
        await run_poll(attempt.id)
        await asyncio.sleep(0.2)
    return await status_of(gen_id)


async def test_reframe_runs_as_a_free_take_with_lineage(client):
    up = (await client.post("/uploads", files={"file": ("clip.mp4", CLIP.read_bytes(), "video/mp4")})).json()
    body = {"model_id": "video-tools", "mode": "reframe", "params": {"aspect_ratio": "1:1", "fit": "pad"},
            "media": [{"role": "input_video", "asset_id": up["id"]}], "route_policy": "cheapest_healthy"}
    est = (await client.post("/registry/estimate", json=body)).json()
    assert est["chosen_route_id"] == "video-tools@local_tools" and est["estimate_usd"] == 0
    chosen = next(c for c in est["candidates"] if c["route_id"] == est["chosen_route_id"])
    assert chosen["provider_kind"] == "local" and chosen["eta_seconds"] > 2

    gen = (await client.post("/generations", json=body)).json()
    done = await drive_local(uuid.UUID(gen["id"]))
    assert done.status == GenStatus.succeeded, done.error_message
    out = (await client.get(f"/generations/{gen['id']}")).json()["assets"][0]
    assert out["width"] == out["height"]
    lineage = (await client.get(f"/assets/{out['id']}/lineage")).json()
    assert lineage["parents"][0]["parent_asset_id"] == up["id"]


async def test_tool_errors_fail_the_take_with_the_reason(client):
    up = (await client.post("/uploads", files={"file": ("clip.mp4", CLIP.read_bytes(), "video/mp4")})).json()
    gen = (await client.post("/generations", json={
        "model_id": "video-tools", "mode": "trim", "params": {"start": 2.9, "end": 2.95},
        "media": [{"role": "input_video", "asset_id": up["id"]}], "route_policy": "cheapest_healthy"})).json()
    done = await drive_local(uuid.UUID(gen["id"]))
    assert done.status == GenStatus.failed and "empty" in (done.error_message or "")
