"""Frame grabs (continue a shot from its last frame) and clip assembly, on real ffmpeg."""

from __future__ import annotations

import os
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


async def test_a_generic_upload_type_is_read_from_the_file_name(client):
    """Scripts and some tools send every file as application/octet-stream: the extension says what it is."""
    files = {"clip.mp4": (CLIP, "video", "video/mp4"),
             "keyframe.png": (ROOT / "fixtures" / "images" / "keyframe.png", "image", "image/png"),
             "line.mp3": (ROOT / "fixtures" / "audio" / "line.mp3", "audio", "audio/mpeg")}
    for name, (path, kind, mime) in files.items():
        r = await client.post("/uploads", files={"file": (name, path.read_bytes(), "application/octet-stream")})
        assert r.status_code == 201, (name, r.text)
        assert (r.json()["kind"], r.json()["mime"]) == (kind, mime)
        assert r.json()["url"].endswith(Path(name).suffix)
    # the file keeps its own extension when it agrees with the type, not the type's usual one
    person = (ROOT / "fixtures" / "images" / "person.jpg").read_bytes()
    r = await client.post("/uploads", files={"file": ("person.jpeg", person, "application/octet-stream")})
    assert r.status_code == 201 and r.json()["url"].endswith("original.jpeg") and r.json()["mime"] == "image/jpeg"
    from studio.api.routers.assets import _extension, _sent_or_guessed
    names = ["movie.mkv", "a.m4v", "a.ogg", "a.opus", "a.mpg", "b.jpeg"]
    assert [_extension(_sent_or_guessed(None, n), n) for n in names] == [".mkv", ".m4v", ".ogg", ".opus", ".mpg", ".jpeg"]
    assert _extension("video/mp4", "clip.bin") == ".mp4" and _extension("image/jpeg", "") == ".jpg"
    # a real type still wins, and a file with neither is refused
    r = await client.post("/uploads", files={"file": ("clip.bin", CLIP.read_bytes(), "video/mp4")})
    assert r.status_code == 201 and r.json()["kind"] == "video"
    r = await client.post("/uploads", files={"file": ("notes", b"hello", "application/octet-stream")})
    assert r.status_code == 415


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


async def test_tags_build_the_driving_clip_library(client):
    up = (await client.post("/uploads", files={"file": ("dance.mp4", CLIP.read_bytes(), "video/mp4")})).json()
    r = await client.patch(f"/assets/{up['id']}", json={"add_tags": ["driving"]})
    assert r.json()["tags"] == ["driving"]
    r = await client.patch(f"/assets/{up['id']}", json={"add_tags": ["driving", "dance"]})
    assert r.json()["tags"] == ["driving", "dance"]
    listed = (await client.get("/assets", params={"tag": "driving", "kind": "video"})).json()["items"]
    assert up["id"] in [a["id"] for a in listed]
    assert all("driving" in a["tags"] for a in listed)


async def test_uploads_are_served_as_media_and_never_as_pages(client, tmp_path, monkeypatch):
    """A file named .html with a vague audio type, an SVG, a playlist pointing at other files on the server, and a
    media key that climbs out of the user's folder."""
    line = (ROOT / "fixtures" / "audio" / "line.mp3").read_bytes()
    r = await client.post("/uploads", files={"file": ("song.html", line, "audio/x-anything")})
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    assert not url.endswith(".html")  # never stored under a name /media would serve as a web page
    served = await client.get(url)
    assert served.status_code == 200
    assert served.headers["content-security-policy"] == "sandbox"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "html" not in served.headers["content-type"]

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert (await client.post("/uploads", files={"file": ("a.svg", svg, "image/svg+xml")})).status_code == 415

    from studio.storage.local import get_storage

    near = Path(os.path.relpath(CLIP, get_storage().temp_dir())).as_posix()  # relative to where uploads are probed
    playlist = f"#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\n{near}\n#EXT-X-ENDLIST\n".encode()
    from studio.api.routers import assets

    async def never(path):  # refused by its first bytes: ffprobe never opens the files it names
        raise AssertionError("a playlist was probed")

    monkeypatch.setattr(assets, "probe", never)
    concat = f"ffconcat version 1.0\nfile '{near}'\n".encode()
    mpd = b'<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>'
    for name, body in (("x.m3u8", playlist), ("list.txt", concat), ("x.mpd", mpd)):
        r = await client.post("/uploads", files={"file": (name, body, "video/x-anything")})
        assert r.status_code == 415 and "playlist" in r.text, name
    monkeypatch.undo()

    me = url.split("/media/")[1].split("/")[1]
    for key in (f"u/{me}/../../tmp/x", f"u/{me}/a/../../{me}x/a/b"):
        assert (await client.get(f"/media/{key}")).status_code == 404


async def test_an_upload_too_large_is_refused_before_it_is_read(client):
    from studio.api.main import MAX_BODY_BYTES

    big = {"content-length": str(MAX_BODY_BYTES + 1), "content-type": "multipart/form-data; boundary=x"}
    r = await client.post("/uploads", content=b"--x--", headers=big)
    assert r.status_code == 413


async def test_abandoned_render_frames_are_swept(db, in_memory_queue):
    from datetime import UTC, datetime, timedelta

    from studio.storage.local import get_storage
    from studio.worker.tasks.maintenance import reap

    folder = get_storage().temp_dir() / "renders"
    old, fresh = folder / "abandoned-test", folder / "in-progress-test"
    for d in (old, fresh):
        d.mkdir(parents=True, exist_ok=True)
        (d / "0001.jpg").write_bytes(b"x")
    two_days = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(old, (two_days, two_days))
    await reap(datetime.now(UTC))
    assert not old.exists() and fresh.exists()
    shutil.rmtree(fresh)
