"""The Cinema cut end to end: takes and title cards in order with cuts and fades, and dialogue, music and SFX
tracks; the exported film has the planned length and order (the preview plans it the same way)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from PIL import Image
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import Asset
from studio.db.session import session_scope
from studio.media.ffmpeg import ffmpeg, probe
from studio.media.timeline import FPS, plan
from studio.storage.local import get_storage

RED, GREEN, BLUE, CARD, LATER = "#c81e1e", "#1ec81e", "#1e1ec8", "#101828", "#f0e6c8"


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def upload(client, path) -> dict:
    r = await client.post("/uploads", files={"file": (path.name, path.read_bytes())})
    assert r.status_code == 201, r.text
    return r.json()


async def solid(tmp_path, name: str, color: str, seconds: float, tone: int | None) -> dict:
    dest = tmp_path / f"{name}.mp4"
    audio = ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}"] if tone else []
    await ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=24:d={seconds}", *audio,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-shortest"] if tone else []), str(dest))
    return dest


async def sound(tmp_path, name: str, freq: int, seconds: float):
    dest = tmp_path / f"{name}.mp3"
    await ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-c:a", "libmp3lame", str(dest))
    return dest


async def color_at(film, seconds: float, tmp_path) -> tuple[int, int, int]:
    still = tmp_path / f"at_{seconds:.3f}.png"
    await ffmpeg("-ss", f"{seconds:.3f}", "-i", str(film), "-frames:v", "1", str(still))
    with Image.open(still) as im:
        return im.convert("RGB").getpixel((8, 8))  # a corner: a card's text sits in the middle


def near(rgb: tuple[int, int, int], hex_color: str, tol: int = 40) -> bool:
    want = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return all(abs(a - b) <= tol for a, b in zip(rgb, want, strict=True))


async def test_the_export_has_the_planned_length_order_and_tracks(client, in_memory_queue, tmp_path):
    red = await upload(client, await solid(tmp_path, "red", RED, 3.0, 440))
    green = await upload(client, await solid(tmp_path, "green", GREEN, 2.0, None))
    blue = await upload(client, await solid(tmp_path, "blue", BLUE, 4.0, 660))
    music = await upload(client, await sound(tmp_path, "music", 220, 12.0))
    line = await upload(client, await sound(tmp_path, "line", 880, 1.5))
    hit = await upload(client, await sound(tmp_path, "hit", 1200, 0.4))
    p = (await client.post("/projects", json={"title": "Colours", "document": {}})).json()

    items = [
        {"card": {"text": "Act one", "subtext": "The harbour", "duration": 2.0, "background": CARD}},
        {"take": {"asset_id": red["id"], "trim_in": 0.5}},
        {"take": {"asset_id": green["id"], "transition": "fade", "volume": 0}},
        {"card": {"text": "Later", "duration": 1.5, "background": LATER, "color": "#101010", "transition": "fade"}},
        {"take": {"asset_id": blue["id"], "trim_out": 2.5}},
    ]
    audio = [
        {"asset_id": music["id"], "track": "music", "start": 0, "volume": 0.6, "fade_in": 1, "fade_out": 2},
        {"asset_id": line["id"], "track": "dialogue", "start": 3.0},
        {"asset_id": hit["id"], "track": "sfx", "start": 5.25, "volume": 0.8},
        {"asset_id": hit["id"], "track": "sfx", "start": 99},  # after the end: left out, never an error
    ]
    run = await start(client, "cinema_export", project_id=p["id"], title="Colours", fade_seconds=0.5, items=items,
                      audio=audio)
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]

    # the same plan the Cut tab's preview makes
    expected = plan([2.0, 2.5, 2.0, 1.5, 2.5], ["cut", "cut", "fade", "fade", "cut"], 0.5)
    assert done["output"]["frames"] == expected.frames
    async with session_scope() as s:
        film_asset = await s.get(Asset, uuid.UUID(done["output"]["asset_id"]))
    film = get_storage().path_for(film_asset.storage_key)
    info = await probe(film)
    assert info.has_audio and info.width == 320 and info.height == 180
    assert abs(info.duration_ms / 1000 - expected.seconds) <= 2 / FPS

    # each item shows up where the plan puts it (sampled clear of the fades)
    segs = expected.segments
    nexts = [nxt.overlap for nxt in segs[1:]] + [0]
    mids = [(seg.start + (seg.overlap + seg.frames - after) / 2) / FPS for seg, after in zip(segs, nexts, strict=True)]
    colors = [await color_at(film, t, tmp_path) for t in mids]
    for got, want in zip(colors, [CARD, RED, GREEN, LATER, BLUE], strict=True):
        assert near(got, want), (got, want)
    assert film_asset.metadata_["starts"] == [seg.start for seg in expected.segments]


async def test_the_cut_lives_in_the_document_and_follows_the_storyboard(client, in_memory_queue, tmp_path):
    music = await upload(client, await sound(tmp_path, "bed", 300, 3.0))
    doc = {"scenes": [{"id": "cone", "shots": [{"id": "sone", "action": "a"}, {"id": "stwo", "action": "b"}]}],
           "cut": {"items": [{"id": "ifirst", "card": {"text": "Title"}}, {"id": "ishot", "shot_id": "stwo"},
                             {"id": "iother", "shot_id": "sone", "volume": 0}],
                   "audio": [{"id": "abed", "track": "music", "asset_id": music["id"], "start": 1.0},
                             {"id": "aline", "track": "dialogue", "asset_id": music["id"], "shot_id": "stwo"}]}}
    p = (await client.post("/projects", json={"title": "Order", "document": doc})).json()
    assert [i["id"] for i in p["document"]["cut"]["items"]] == ["ifirst", "ishot", "iother"]

    # deleting a shot takes it out of the cut, with the dialogue tied to it
    doc["scenes"][0]["shots"] = [{"id": "sone", "action": "a"}]
    r = await client.put(f"/projects/{p['id']}", json={"version": 1, "document": doc})
    cut = r.json()["document"]["cut"]
    assert [i["id"] for i in cut["items"]] == ["ifirst", "iother"] and [a["id"] for a in cut["audio"]] == ["abed"]

    stranger = {**doc, "cut": {"audio": [{"track": "sfx", "asset_id": str(uuid.uuid4())}]}}
    r = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": stranger})
    assert r.status_code == 422 and "assets not found" in r.text

    # a sound the cut plays can't be deleted; once it's out, restoring an old version that had it drops it
    assert (await client.delete(f"/assets/{music['id']}")).status_code == 409
    r = await client.put(f"/projects/{p['id']}", json={"version": 2, "document": {**doc, "cut": {**doc["cut"], "audio": []}}})
    assert r.status_code == 200, r.text
    assert (await client.delete(f"/assets/{music['id']}")).status_code == 204
    r = await client.post(f"/projects/{p['id']}/revisions/1/restore")
    assert r.status_code == 200, r.text
    assert r.json()["document"]["cut"]["audio"] == []
