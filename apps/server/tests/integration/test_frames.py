"""Frame grabs (continue a shot from its last frame) and clip assembly, on real ffmpeg."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from studio.api.main import app as api
from studio.media.ffmpeg import probe
from studio.media.mock_media import make_video
from studio.media.ops import concat_clips, extract_frame

ROOT = Path(__file__).resolve().parents[4]
CLIP = ROOT / "fixtures" / "videos" / "clip_3s.mp4"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def test_last_frame_becomes_an_image_asset_once(client):
    up = await client.post("/uploads", files={"file": ("clip.mp4", CLIP.read_bytes(), "video/mp4")})
    video = up.json()
    r = await client.post(f"/assets/{video['id']}/frames", json={"at": "last"})
    assert r.status_code == 200, r.text
    frame = r.json()
    assert frame["kind"] == "image" and (frame["width"], frame["height"]) == (video["width"], video["height"])
    assert frame["title"].startswith("Last frame of")

    again = await client.post(f"/assets/{video['id']}/frames", json={"at": "last"})
    assert again.json()["id"] == frame["id"]  # idempotent

    lineage = (await client.get(f"/assets/{frame['id']}/lineage")).json()
    assert lineage["parents"][0] == {**lineage["parents"][0], "parent_asset_id": video["id"], "relation": "last_frame"}

    not_video = await client.post(f"/assets/{frame['id']}/frames", json={"at": "first"})
    assert not_video.status_code == 422


async def test_frame_positions(tmp_path):
    first, mid = tmp_path / "first.png", tmp_path / "mid.png"
    await extract_frame(CLIP, first, "first")
    await extract_frame(CLIP, mid, 1.5)
    assert first.stat().st_size > 0 and mid.stat().st_size > 0


async def test_concat_joins_mixed_clips_with_and_without_sound(tmp_path):
    a = tmp_path / "a.mp4"
    shutil.copy(CLIP, a)
    b = tmp_path / "b.mp4"  # different size and no audio track
    await make_video(b, tmp_path, model_id="m", mode="t2v", prompt="b", seed=1, duration=2.0, resolution="360p",
                     aspect="16:9", fps=24, audio=False)
    out = tmp_path / "cut.mp4"
    await concat_clips([a, b], out)
    info, ia = await probe(out), await probe(a)
    assert info.has_audio and (info.width, info.height) == (ia.width, ia.height)
    assert info.duration_ms == pytest.approx((ia.duration_ms or 0) + 2000, abs=300)
