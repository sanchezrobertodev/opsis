"""A character exported as a zip and imported again, as another studio would: the same pictures in the same order,
its looks, profile and trained identities, and never an id, a run or a cloned voice. A refused import leaves nothing."""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import httpx
import pytest
from sqlalchemy import func, select
from test_elements import upload

from studio.api.main import app as api
from studio.db.models import Asset, Element
from studio.db.session import session_scope
from studio.domain.derived import save_derived
from studio.domain.enums import AssetKind
from studio.storage.local import get_storage


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def hashes(element: dict) -> list[tuple[str, str | None, bool, str]]:
    """Each picture as (view, look, first, its bytes' hash), in the element's order."""
    async with session_scope() as s:
        rows = {
            str(a.id): a.content_hash
            for a in (
                await s.execute(
                    select(Asset).where(
                        Asset.id.in_([uuid.UUID(i["asset"]["id"]) for i in element["images"]])
                    )
                )
            ).scalars()
        }
    return [(i["view"], i["look"], i["is_primary"], rows[i["asset"]["id"]]) for i in element["images"]]


async def test_an_exported_character_imports_with_its_pictures_looks_profile_and_identity(client):
    pics = [await upload(client, (30 * i, 90, 150)) for i in range(4)]
    el = (
        await client.post(
            "/elements",
            json={
                "name": f"Maya {uuid.uuid4().hex[:6]}",
                "description": "a lighthouse keeper with short grey hair",
                "attributes": {"traits": {"hair": "short grey"}},
                "images": [{"asset_id": pics[0], "view": "front"}, {"asset_id": pics[1], "view": "side"}],
                "profile": {"personality": "dry wit", "catchphrases": ["Steady now."]},
                "voice": {"model_id": "eleven-v4", "params": {"voice_id": "mock-voice-0123456789ab"}},
            },
        )
    ).json()
    winter = {"name": "Winter", "description": "wearing a red wool coat"}
    assert (await client.put(f"/elements/{el['id']}/looks/winter", json=winter)).status_code == 200
    r = await client.post(
        f"/elements/{el['id']}/images",
        json=[{"asset_id": pics[2], "look": "winter"}, {"asset_id": pics[3], "view": "expressions"}],
    )
    assert r.status_code == 200, r.text
    run_id = str(uuid.uuid4())
    async with session_scope() as s:  # a trained identity, and a cloned voice that must never travel
        row = await s.get(Element, uuid.UUID(el["id"]))
        src = get_storage().temp_dir() / f"w-{uuid.uuid4().hex}.safetensors"
        header = b'{"__metadata__": {"format": "pt"}}'
        src.write_bytes(len(header).to_bytes(8, "little") + header)
        weights = await save_derived(
            s,
            row.user_id,
            src,
            kind=AssetKind.lora,
            title="w",
            parents=[],
            metadata={"expert": "low", "base": "flux-3-klein"},
        )
        row.lora = {
            "flux-3-klein": {
                "base": "flux-3-klein",
                "trigger": "mayazqtest",
                "low": str(weights.id),
                "high": None,
                "steps": 1000,
                "run_id": run_id,
                "trained_at": "2026-11-17T12:00:00+00:00",
            }
        }
        row.cloned_voice_id = "cloned-0123456789"
    before = (await client.get(f"/elements/{el['id']}")).json()

    r = await client.get(f"/elements/{el['id']}/export")
    assert r.status_code == 200, r.text
    assert (
        r.headers["content-type"] == "application/zip"
        and f"{el['slug']}.zip" in r.headers["content-disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("character.json"))
        names = z.namelist()
    said = json.dumps(manifest)
    for kept_here in (el["id"], run_id, "cloned-0123456789", "mock-voice-0123456789ab", str(weights.id)):
        assert kept_here not in said
    assert "user_id" not in said and "run_id" not in said
    assert manifest["voice"] == {"model_id": "eleven-v4", "params": {}}
    assert names == [
        "character.json",
        *(i["file"] for i in manifest["images"]),
        "files/lora-flux-3-klein-low.safetensors",
    ]

    r = await client.post("/elements/import", files={"file": ("maya.zip", r.content, "application/zip")})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != el["id"] and new["slug"] == f"{el['slug']}-2" and new["name"] == el["name"]
    for same in ("description", "attributes", "profile", "looks", "kind"):
        assert new[same] == before[same], same
    assert await hashes(new) == await hashes(before)
    assert new["cover"]["id"] == new["images"][0]["asset"]["id"]
    assert new["voice"] == {"model_id": "eleven-v4", "params": {}}
    flux = new["lora"]["flux-3-klein"]
    assert flux["trigger"] == "mayazqtest" and flux["steps"] == 1000 and flux["low"] != str(weights.id)
    assert flux["trained_at"] == "2026-11-17T12:00:00+00:00" and not flux.get("run_id")
    async with session_scope() as s:
        row = await s.get(Element, uuid.UUID(new["id"]))
        lora = await s.get(Asset, uuid.UUID(flux["low"]))
        assert row.cloned_voice_id is None
        assert (
            lora.kind == AssetKind.lora
            and lora.source == "import"
            and lora.content_hash == weights.content_hash
        )

    # the identity loads on FLUX 3 Klein like a trained one
    out = await client.post(
        "/generations",
        json={"model_id": "flux-3-klein", "mode": "t2i", "prompt": f"@{new['slug']} on a pier"},
    )
    assert out.status_code == 201, out.text
    assert [m["asset_id"] for m in out.json()["request"]["media"]] == [flux["low"]]


async def test_an_export_leaves_out_a_lost_picture_and_an_import_a_voice_no_model_here_speaks(client):
    pics = [await upload(client, (60 * i, 90, 150)) for i in range(2)]
    el = (
        await client.post(
            "/elements",
            json={
                "name": f"Juno {uuid.uuid4().hex[:6]}",
                "images": [{"asset_id": p, "view": "front"} for p in pics],
            },
        )
    ).json()
    async with session_scope() as s:  # a database restored without data/media: one picture's file is gone
        lost = await s.get(Asset, uuid.UUID(pics[1]))
        get_storage().path_for(lost.storage_key).unlink()
    r = await client.get(f"/elements/{el['id']}/export")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        members = {name: z.read(name) for name in z.namelist()}
    doc = json.loads(members["character.json"])
    assert [(i["file"], i["type"]) for i in doc["images"]] == [("files/001-front.png", "image/png")]

    # from a studio whose voice is on a model this one has but that doesn't speak
    members["character.json"] = json.dumps(
        {**doc, "voice": {"model_id": "wan-2.2-image", "params": {}}}
    ).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    r = await client.post("/elements/import", files={"file": ("juno.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201, r.text
    assert r.json()["voice"] is None and len(r.json()["images"]) == 1


async def test_a_refused_import_keeps_none_of_its_files(client):
    good = await upload(client)
    async with session_scope() as s:
        picture = await s.get(Asset, uuid.UUID(good))
        data = get_storage().path_for(picture.storage_key).read_bytes()
        user_dir = get_storage().path_for(picture.storage_key).parents[1]
        imported = select(func.count()).select_from(Asset).where(Asset.source == "import")
        count = (await s.execute(imported)).scalar_one()
    folders = len(list(user_dir.iterdir()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "character.json",
            json.dumps(
                {
                    "kind": "character",
                    "name": "Refused",
                    "slug": "refused",
                    "images": [
                        {"file": "files/001-front.png", "primary": True},
                        {"file": "files/002-side.png"},
                    ],
                }
            ),
        )
        z.writestr("files/001-front.png", data)
        # refused by the upload checks once the first picture is kept (ffprobe takes any bytes named .png for an image)
        z.writestr("files/002-side.png", b"#EXTM3U\n#EXTINF:1,\nhttps://example.test/a.ts\n")
    r = await client.post("/elements/import", files={"file": ("c.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 415 and r.json()["detail"].startswith(
        "files/002-side.png in the zip: playlists"
    ), r.text
    async with session_scope() as s:
        assert (await s.execute(imported)).scalar_one() == count
        assert (await s.execute(select(Element).where(Element.name == "Refused"))).first() is None
    assert len(list(user_dir.iterdir())) == folders  # the picture it had kept went too

    r = await client.post("/elements/import", files={"file": ("c.zip", b"not a zip", "application/zip")})
    assert r.status_code == 422 and "zip" in r.json()["detail"]
