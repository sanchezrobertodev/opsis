"""Looks (`@handle:look`), grid sheets a mention leaves out, and casts (saved groups of characters, `@crew`): the
routes, what a mention attaches, the recipes that draw them on the mock provider, and the migration's way back."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text
from test_elements import estimate, make_element, upload
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.db.models import ElementMember, Generation
from studio.db.session import get_engine, session_scope

SERVER_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


async def generate(client, prompt: str, model_id: str = "nano-banana-2", mode: str = "edit", **body) -> dict:
    """The request a take sends (made, not run: nothing drains the queue here)."""
    r = await client.post("/generations", json={"model_id": model_id, "mode": mode, "prompt": prompt, **body})
    assert r.status_code == 201, r.text
    return r.json()["request"]


def refs(request: dict) -> list[str]:
    return [m["asset_id"] for m in request["media"] if m["role"] == "image_references"]


def ids(element: dict) -> list[str]:
    return [i["asset"]["id"] for i in element["images"]]


async def test_a_look_has_its_own_pictures_and_a_plain_mention_never_attaches_them(client):
    maya = await make_element(
        client, "Maya Winterbourne", description="a woman with short black hair", images=2
    )
    at, main = maya["handle"], ids(maya)
    assert maya["looks"] == {} and maya["members"] == [] and {i["look"] for i in maya["images"]} == {None}

    winter = {"name": "Winter", "description": "wearing a red wool coat and boots"}
    r = await client.put(f"/elements/{maya['id']}/looks/winter", json=winter)
    assert r.status_code == 200, r.text
    assert r.json()["looks"] == {"winter": winter}
    assert (await client.put(f"/elements/{maya['id']}/looks/Winter_Coat", json=winter)).status_code == 422
    place = await make_element(client, "Lookless Harbour", kind="location", images=1)
    assert (await client.put(f"/elements/{place['id']}/looks/night", json=winter)).status_code == 422

    coat, grid = await upload(client, (10, 20, 30)), await upload(client, (90, 90, 90))
    r = await client.post(f"/elements/{maya['id']}/images", json=[{"asset_id": coat, "look": "summer"}])
    assert r.status_code == 422 and "no look called summer" in r.text
    r = await client.post(
        f"/elements/{maya['id']}/images",
        json=[
            {"asset_id": coat, "view": "full_body", "look": "winter"},
            {"asset_id": grid, "view": "expressions"},
        ],
    )
    assert r.status_code == 200, r.text
    images = {i["asset"]["id"]: i for i in r.json()["images"]}
    assert (
        images[coat]["look"] == "winter" and not images[coat]["is_primary"] and images[grid]["look"] is None
    )
    assert r.json()["cover"]["id"] == main[0]

    # a plain mention: the main pictures only, never a look's or a grid sheet
    plain = await generate(client, f"{at} waves")
    assert refs(plain) == main
    assert plain["mentions"][0]["look"] is None and plain["mentions"][0]["attached"] == main
    # wearing the look: its pictures first, then the main ones up to two, and its words after the character's
    worn = await generate(client, f"{at}:winter waves, then {at} smiles")
    assert refs(worn) == [coat, main[0]]
    assert worn["mentions"][0]["look"] == "winter" and worn["mentions"][0]["attached"] == [coat, main[0]]
    assert worn["compiled_prompt"].startswith(
        "Maya Winterbourne, a woman with short black hair, wearing a red wool "
        "coat and boots, waves, then Maya Winterbourne smiles"
    )
    # a look it doesn't have: shown as it is, and the word stays readable
    odd = await generate(client, f"{at}:summer waves")
    assert refs(odd) == main and odd["mentions"][0]["look"] is None
    assert odd["compiled_prompt"].startswith("Maya Winterbourne, a woman with short black hair, summer waves")
    assert any("no look called summer" in w for w in odd["warnings"])

    # a picture moves between the main ones and a look's, and can go first
    r = await client.patch(f"/elements/{maya['id']}/images/{main[1]}", json={"look": "winter"})
    assert r.status_code == 200, r.text
    assert refs(await generate(client, f"{at} waves")) == [main[0]]
    r = await client.patch(
        f"/elements/{maya['id']}/images/{main[1]}", json={"look": None, "primary": True, "view": "side"}
    )
    moved = {i["asset"]["id"]: i for i in r.json()["images"]}[main[1]]
    assert (moved["look"], moved["is_primary"], moved["view"]) == (None, True, "side")
    assert refs(await generate(client, f"{at} waves")) == [main[1], main[0]]
    assert (
        await client.patch(f"/elements/{maya['id']}/images/{main[1]}", json={"look": "summer"})
    ).status_code == 422
    assert (
        await client.patch(f"/elements/{maya['id']}/images/{uuid.uuid4()}", json={"view": "side"})
    ).status_code == 404
    # the cover made a look's picture stops covering: the first main picture does
    r = await client.patch(f"/elements/{maya['id']}/images/{main[0]}", json={"look": "winter"})
    assert r.status_code == 200 and r.json()["cover"]["id"] == main[1]

    # forgetting the look takes its pictures off the character; they stay in the library
    r = await client.delete(f"/elements/{maya['id']}/looks/winter")
    assert r.status_code == 200 and r.json()["looks"] == {}
    assert coat not in ids(r.json()) and grid in ids(r.json())
    assert (await client.get(f"/assets/{coat}")).status_code == 200
    assert (await client.delete(f"/elements/{maya['id']}/looks/winter")).status_code == 404


async def test_a_cast_stands_for_its_characters(client):
    a = await make_element(client, "Ines Crewe", description="a woman in a yellow raincoat", images=2)
    b = await make_element(client, "Kofi Crewe", description="a tall man with a beard", images=2)
    c = await make_element(client, "Rui Crewe", images=1)
    place = await make_element(client, "Crewe Harbour", kind="location", images=1)

    async def make(**body):
        return await client.post("/elements", json={"kind": "cast", "name": "The Crewes", **body})

    for members, why in (
        ([a["id"]], "2 to 8"),
        ([a["id"], a["id"]], "2 to 8"),
        ([a["id"], place["id"]], "only your characters"),
        ([a["id"], str(uuid.uuid4())], "only your characters"),
    ):
        r = await make(members=members)
        assert r.status_code == 422 and why in r.text, r.text
    assert (await make()).status_code == 422  # a cast needs its characters
    r = await client.post(
        "/elements", json={"kind": "character", "name": "Solo Crewe", "members": [a["id"], b["id"]]}
    )
    assert r.status_code == 422  # and only a cast has them

    r = await make(description="three cousins", members=[a["id"], b["id"], c["id"]])
    assert r.status_code == 201, r.text
    crew = r.json()
    assert crew["kind"] == "cast" and crew["images"] == [] and crew["looks"] == {}
    assert [m["id"] for m in crew["members"]] == [a["id"], b["id"], c["id"]]
    assert (
        crew["members"][0]["handle"] == a["handle"] and crew["members"][0]["cover"]["id"] == a["cover"]["id"]
    )
    assert crew["id"] in [e["id"] for e in (await client.get("/elements", params={"kind": "cast"})).json()]

    # each character attaches a share of the room, two at most, and the mention names them all
    at, ia, ib, ic, ip = crew["handle"], ids(a), ids(b), ids(c), ids(place)
    roomy = await generate(client, f"{at} walk into {place['handle']}")
    assert refs(roomy) == [*ia, *ib, *ic, *ip]
    by = {m["slug"]: m for m in roomy["mentions"]}
    assert by[crew["slug"]]["attached"] == [*ia, *ib, *ic] and by[place["slug"]]["attached"] == ip
    e = await estimate(
        client,
        model_id="seedance-2.5",
        mode="omni_reference",
        prompt=f"{at} walk into {place['handle']}",
        params={"duration": 4},
    )
    assert e["compiled_prompt"].startswith(
        "Ines Crewe (@Image1, @Image2), Kofi Crewe (@Image3, @Image4), Rui Crewe "
        "(@Image5), three cousins, walk into Crewe Harbour (@Image6)"
    )
    # where no picture shows them, each is described after their name, as when mentioned on their own
    words = await estimate(
        client, model_id="veo-3.1", mode="t2v", prompt=f"{at} walk in", params={"duration": 8}
    )
    assert words["compiled_prompt"].startswith(
        "Ines Crewe, a woman in a yellow raincoat, Kofi Crewe, a tall man with a beard, Rui Crewe, "
        "three cousins, walk in"
    )
    tight = await generate(
        client,
        f"{at} walk into {place['handle']}",
        model_id="veo-3.1",
        mode="reference",
        params={"duration": 8},
    )
    assert refs(tight) == [ia[0], ib[0], ic[0]]  # three slots: one each
    assert any(f"no reference slots left for {place['handle']}" in w for w in tight["warnings"])
    # one of them mentioned on their own has their pictures already: the cast attaches the others
    both = await generate(client, f"{b['handle']} leads {at}")
    assert refs(both) == [*ib, *ia, *ic]
    by = {m["slug"]: m for m in both["mentions"]}
    assert by[b["slug"]]["attached"] == ib and by[crew["slug"]]["attached"] == [*ia, *ic]
    attached = [x for m in both["mentions"] for x in m["attached"]]
    assert len(attached) == len(set(attached))  # each listed once, so Another take drops each copy once

    # the members can be replaced, in a new order; a cast stays a cast, and nothing else becomes one
    r = await client.patch(f"/elements/{crew['id']}", json={"members": [c["id"], a["id"], b["id"]]})
    assert r.status_code == 200 and [m["id"] for m in r.json()["members"]] == [c["id"], a["id"], b["id"]]
    assert (await client.patch(f"/elements/{crew['id']}", json={"members": [c["id"]]})).status_code == 422
    assert (
        await client.patch(f"/elements/{a['id']}", json={"members": [b["id"], c["id"]]})
    ).status_code == 422
    assert (await client.patch(f"/elements/{crew['id']}", json={"kind": "character"})).status_code == 422
    assert (await client.patch(f"/elements/{a['id']}", json={"kind": "cast"})).status_code == 422

    # a character that stops being one, or is deleted, leaves the casts it was in
    assert (await client.patch(f"/elements/{b['id']}", json={"kind": "prop"})).status_code == 200
    assert (await client.delete(f"/elements/{c['id']}")).status_code == 204
    assert [m["id"] for m in (await client.get(f"/elements/{crew['id']}")).json()["members"]] == [a["id"]]
    async with session_scope() as s:
        gone = [uuid.UUID(b["id"]), uuid.UUID(c["id"])]
        left = await s.scalar(
            select(func.count()).select_from(ElementMember).where(ElementMember.member_id.in_(gone))
        )
    assert left == 0
    # deleting the cast lets its characters go; they stay
    assert (await client.delete(f"/elements/{crew['id']}")).status_code == 204
    assert (await client.get(f"/elements/{a['id']}")).status_code == 200
    async with session_scope() as s:
        left = await s.scalar(
            select(func.count())
            .select_from(ElementMember)
            .where(ElementMember.group_id == uuid.UUID(crew["id"]))
        )
    assert left == 0


async def test_a_look_is_drawn_from_the_character_and_worn_by_its_mention(client, in_memory_queue):
    odile = await make_element(client, "Odile Lookwood", description="a woman with red curls", images=2)
    at, main = odile["handle"], ids(odile)
    r = await client.post(
        "/pipelines/estimate",
        json={
            "recipe": "character_look",
            "input": {"character": "nobody-here", "look": "winter", "name": "Winter", "outfit": "a red coat"},
        },
    )
    assert r.status_code == 422 and "no saved character called @nobody-here" in r.text

    run = await start(
        client,
        "character_look",
        character=odile["slug"],
        look="winter",
        name="Winter",
        outfit="a red wool coat and boots",
        count=2,
    )
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    made = done["output"]["asset_ids"]
    assert done["output"]["look"] == "winter" and len(made) == 2
    after = (await client.get(f"/elements/{odile['id']}")).json()
    assert after["looks"] == {
        "winter": {"name": "Winter", "description": "wearing a red wool coat and boots"}
    }
    kept = {i["asset"]["id"]: i for i in after["images"]}
    assert all(kept[a]["look"] == "winter" and not kept[a]["is_primary"] for a in made)
    assert after["cover"]["id"] == odile["cover"]["id"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        drawn = await s.get(Generation, uuid.UUID(steps["look[00]"]["generation_id"]))
        assert drawn is not None and drawn.mode == "edit"
        assert [m["asset_id"] for m in drawn.request["media"]] == main  # drawn from its main pictures

    worn = await generate(client, f"{at}:winter at the station")
    assert set(refs(worn)) == set(made)
    assert refs(await generate(client, f"{at} at the station")) == main


async def test_an_expression_sheet_is_kept_but_never_attached(client, in_memory_queue):
    paz = await make_element(client, "Paz Gridley", images=2)
    gone, main = ids(paz)
    # its cover taken off: the sheet still never becomes the cover
    assert (await client.delete(f"/elements/{paz['id']}/images/{gone}")).status_code == 200
    run = await start(client, "character_sheet", element_id=paz["id"], slug=paz["slug"], sheet="expressions")
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    sheet = done["output"]["asset_id"]
    after = (await client.get(f"/elements/{paz['id']}")).json()
    kept = {i["asset"]["id"]: i for i in after["images"]}
    assert (kept[sheet]["view"], kept[sheet]["is_primary"], kept[sheet]["look"]) == (
        "expressions",
        False,
        None,
    )
    assert after["cover"]["id"] == main
    assert refs(await generate(client, f"{paz['handle']} laughs")) == [main]


async def test_a_run_draws_only_from_what_a_plain_mention_attaches(client):
    """Runs that draw from a character's pictures count and take its main ones: never a look's or a grid sheet."""
    nell = await make_element(client, "Nell Plainview", images=2)
    first, main = ids(nell)
    await client.put(
        f"/elements/{nell['id']}/looks/winter", json={"name": "Winter", "description": "in a coat"}
    )
    # her first picture becomes the look's: no main picture is primary now, and the look's sorts first
    r = await client.patch(f"/elements/{nell['id']}/images/{first}", json={"look": "winter"})
    assert r.status_code == 200 and not any(i["is_primary"] for i in r.json()["images"])
    picture = await upload(client, (90, 30, 30))
    run = await start(client, "face_swap", picture_asset_id=picture, character=nell["slug"], consent=True)
    assert run["input"]["face_asset_id"] == main

    # with only the look's picture and a grid sheet left, nothing is drawn from her
    assert (await client.delete(f"/elements/{nell['id']}/images/{main}")).status_code == 200
    grid = await upload(client, (90, 90, 90))
    r = await client.post(f"/elements/{nell['id']}/images", json=[{"asset_id": grid, "view": "expressions"}])
    assert r.status_code == 200 and r.json()["cover"] is None  # neither covers
    r = await client.post(
        "/pipelines/estimate",
        json={
            "recipe": "character_look",
            "input": {
                "character": nell["slug"],
                "look": "summer",
                "name": "Summer",
                "outfit": "a linen shirt",
            },
        },
    )
    assert r.status_code == 422 and "has no pictures" in r.text


async def test_a_grid_sheet_never_comes_first_or_covers(client):
    grid, face = await upload(client, (90, 90, 90)), await upload(client, (10, 20, 30))
    r = await client.post(
        "/elements",
        json={
            "name": "Gridda First",
            "images": [{"asset_id": grid, "view": "expressions"}, {"asset_id": face}],
        },
    )
    assert r.status_code == 201, r.text
    gridda = r.json()
    assert gridda["cover"]["id"] == face
    assert {i["asset"]["id"]: i["is_primary"] for i in gridda["images"]} == {grid: False, face: True}

    path = f"/elements/{gridda['id']}/images"
    assert (await client.patch(f"{path}/{grid}", json={"primary": True})).status_code == 422
    r = await client.patch(f"{path}/{face}", json={"view": "poses"})
    assert r.status_code == 200, r.text
    assert r.json()["cover"] is None and not any(i["is_primary"] for i in r.json()["images"])


async def test_the_migration_goes_down_and_up_again(client):
    """Down to before looks and casts (casts, look pictures and grid sheets go, everything else stays), then up
    again."""
    from alembic import command
    from alembic.config import Config

    vera = await make_element(client, "Vera Downgrade", images=1)
    pal = await make_element(client, "Pal Downgrade", images=1)
    await client.put(
        f"/elements/{vera['id']}/looks/winter", json={"name": "Winter", "description": "in a coat"}
    )
    coat, grid = await upload(client), await upload(client, (90, 90, 90))
    r = await client.post(
        f"/elements/{vera['id']}/images",
        json=[{"asset_id": coat, "look": "winter"}, {"asset_id": grid, "view": "expressions"}],
    )
    assert r.status_code == 200
    crew = await client.post(
        "/elements", json={"kind": "cast", "name": "Downgrade Pair", "members": [vera["id"], pal["id"]]}
    )
    assert crew.status_code == 201, crew.text

    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    await get_engine().dispose()  # no pooled connection may hold a lock the migration waits for
    await asyncio.to_thread(command.downgrade, cfg, "de8f9a0b1c2d")
    try:
        async with session_scope() as s:
            columns = set(
                (
                    await s.execute(
                        text(
                            "SELECT table_name || '.' || column_name FROM "
                            "information_schema.columns WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
            assert "element_assets.look" not in columns and "elements.looks" not in columns
            assert await s.scalar(text("SELECT to_regclass('element_members')")) is None
            assert await s.scalar(text("SELECT count(*) FROM elements WHERE kind = 'cast'")) == 0
            links = set(
                (
                    await s.execute(
                        text("SELECT asset_id FROM element_assets WHERE element_id = :e"),
                        {"e": uuid.UUID(vera["id"])},
                    )
                ).scalars()
            )
            # the look's picture and the grid sheet left (old code would attach both); the assets stay
            assert links == {uuid.UUID(ids(vera)[0])}
            assert (
                await s.scalar(
                    text("SELECT count(*) FROM assets WHERE id IN (:a, :b)"),
                    {"a": uuid.UUID(coat), "b": uuid.UUID(grid)},
                )
                == 2
            )
    finally:
        await get_engine().dispose()
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await get_engine().dispose()
    again = (await client.get(f"/elements/{vera['id']}")).json()
    assert again["looks"] == {} and ids(again) == ids(vera)
