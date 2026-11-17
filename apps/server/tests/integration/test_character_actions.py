"""A character's profile (saved field by field, drafted by Claude, read by the writers), the likeness check (Claude
compares a take with the character's pictures and the answer is kept on the take), and the quick actions on the mock
provider: a 3D figure (`character_figure`) and a saved character saying hello (`talking_shot` with `character`).
Claude is mocked; one live check uses a fake client, so nothing reaches Anthropic."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_elements import make_element, upload
from test_enhance import FakeMessages, ledger
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.api.routers import elements as elements_router
from studio.api.routers import projects as projects_router
from studio.budget.guard import BudgetBlocked
from studio.characters.likeness import Difference, Likeness
from studio.characters.traits import CREATURE
from studio.config import get_settings
from studio.db.models import Generation
from studio.db.session import session_scope
from studio.llm import client as llm_client
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.images import ImageError
from studio.pipelines import llm_ops

EMPTY = {"personality": "", "backstory": "", "catchphrases": [], "speech": ""}
PROFILE = {
    "personality": "warm, nosy and quick to laugh",
    "catchphrases": ["Butter fixes everything."],
    "speech": "fast, with baking metaphors",
}


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


def ids(element: dict) -> list[str]:
    return [i["asset"]["id"] for i in element["images"]]


async def take(client, queue, prompt: str) -> str:
    """A picture made on the mock from a prompt (mentions attach their pictures, as in any take)."""
    r = await client.post(
        "/generations", json={"model_id": "nano-banana-2", "mode": "edit", "prompt": prompt}
    )
    assert r.status_code == 201, r.text
    await drain(queue)
    detail = (await client.get(f"/generations/{r.json()['id']}")).json()
    assert detail["status"] == "succeeded", detail
    return detail["assets"][0]["id"]


# ---------------------------------------------------------------- the profile


async def test_a_characters_profile_is_saved_field_by_field(client):
    bea = await make_element(client, "Bea Baker", description="a baker with flour on her apron")
    assert bea["profile"] == EMPTY
    path = f"/elements/{bea['id']}"
    r = await client.patch(
        path,
        json={
            "profile": {
                "personality": PROFILE["personality"],
                "catchphrases": ["Butter fixes everything.", " "],
            }
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["profile"] == {
        **EMPTY,
        "personality": PROFILE["personality"],
        "catchphrases": ["Butter fixes everything."],
    }
    # the fields sent replace those saved; the others are kept, and so is everything else about her
    r = await client.patch(
        path, json={"profile": {"speech": PROFILE["speech"], "backstory": "Grew up above the shop."}}
    )
    assert r.json()["profile"] == {**PROFILE, "backstory": "Grew up above the shop."}
    r = await client.patch(
        path, json={"description": "a baker", "attributes": {"traits": {"age": "elderly"}}}
    )
    assert r.json()["profile"]["speech"] == PROFILE["speech"] and r.json()["attributes"]["traits"] == {
        "age": "elderly"
    }
    r = await client.patch(path, json={"profile": {"catchphrases": []}})
    assert (
        r.json()["profile"]["catchphrases"] == []
        and r.json()["profile"]["personality"] == PROFILE["personality"]
    )

    for bad in (
        {"personality": "x" * 301},
        {"backstory": "x" * 601},
        {"speech": "x" * 201},
        {"catchphrases": [f"line {i}" for i in range(6)]},
        {"catchphrases": ["x" * 121]},
        {"mood": "happy"},
    ):
        assert (await client.patch(path, json={"profile": bad})).status_code == 422, bad
    assert (await client.get(path)).json()["profile"]["speech"] == PROFILE["speech"]  # nothing half-saved

    # characters only: not another kind, not a cast, not a character turning into something else
    place = await make_element(client, "Profile Harbour", kind="location")
    r = await client.patch(f"/elements/{place['id']}", json={"profile": PROFILE})
    assert r.status_code == 422 and "only characters have a profile" in r.text
    pal = await make_element(client, "Pal Baker")
    crew = await client.post(
        "/elements", json={"kind": "cast", "name": "Bakers", "members": [bea["id"], pal["id"]]}
    )
    assert crew.status_code == 201, crew.text
    assert (
        await client.patch(f"/elements/{crew.json()['id']}", json={"profile": PROFILE})
    ).status_code == 422
    assert (await client.patch(path, json={"kind": "prop", "profile": PROFILE})).status_code == 422
    assert crew.json()["profile"] == EMPTY

    # a new character can bring one
    r = await client.post("/elements", json={"name": "Kai Fisher", "profile": PROFILE})
    assert r.status_code == 201 and r.json()["profile"] == {**EMPTY, **PROFILE}
    r = await client.post("/elements", json={"kind": "prop", "name": "Rolling pin", "profile": PROFILE})
    assert r.status_code == 422


async def test_claude_drafts_a_profile_and_saves_nothing(client):
    mara = await make_element(client, "Mara Quill", description="A tall woman in a red coat.")
    path = f"/elements/{mara['id']}/profile/draft"
    r = await client.post(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["cost_usd"] == 0 and body["model"].endswith("(mock)")
    draft = body["profile"]
    assert draft["personality"].startswith("Mara is") and "a tall woman in a red coat" in draft["backstory"]
    assert draft["catchphrases"] and draft["speech"]
    assert (await client.post(path)).json() == body  # the mock is the same each time
    assert (await client.get(f"/elements/{mara['id']}")).json()["profile"] == EMPTY  # nothing saved

    place = await make_element(client, "Draft Harbour", kind="location")
    assert (await client.post(f"/elements/{place['id']}/profile/draft")).status_code == 422
    assert (await client.post(f"/elements/{uuid.uuid4()}/profile/draft")).status_code == 404
    calls = (await client.get("/llm/calls")).json()["calls"]
    assert {"character.profile", "character.check"} <= set(calls)
    assert all(0 < calls[c]["usd"] < 1 for c in ("character.profile", "character.check"))


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BudgetBlocked("over today's limit"), 402),
        (LLMUnavailable("no Anthropic key"), 503),
        (LLMError("Claude refused"), 502),
    ],
)
async def test_why_claude_couldnt_help_comes_with_its_status(client, monkeypatch, error, status):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(elements_router, "draft_profile", fail)
    monkeypatch.setattr(elements_router, "check_likeness", fail)
    vic = await make_element(client, "Vic Errors", images=1)
    picture = await upload(client, (10, 200, 10))
    r = await client.post(f"/elements/{vic['id']}/profile/draft")
    assert r.status_code == status and error.args[0] in r.json()["detail"]
    r = await client.post(f"/elements/{vic['id']}/check", json={"asset_id": picture})
    assert r.status_code == status and error.args[0] in r.json()["detail"]


async def test_a_picture_claude_cant_read_is_a_422(client, monkeypatch):
    async def unreadable(*args, **kwargs):
        raise ImageError("couldn't read it")  # an LLMError too, answered before the 502

    monkeypatch.setattr(elements_router, "check_likeness", unreadable)
    una, picture = await make_element(client, "Una Unreadable"), await upload(client, (70, 70, 140))
    r = await client.post(f"/elements/{una['id']}/check", json={"asset_id": picture})
    assert r.status_code == 422 and "couldn't read it" in r.text


async def test_the_writers_get_the_profile_where_they_write_lines(client, in_memory_queue, monkeypatch):
    bea = await make_element(client, "Bea Lines", description="a baker")
    await client.patch(f"/elements/{bea['id']}", json={"profile": PROFILE})
    seen: dict[str, dict] = {}

    def spy(name, real):
        async def wrapped(*args, **kwargs):
            seen[name] = kwargs
            return await real(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(llm_ops, "write_vlog", spy("vlog", llm_ops.write_vlog))
    monkeypatch.setattr(llm_ops, "write_script", spy("faceless", llm_ops.write_script))
    monkeypatch.setattr(projects_router, "plan_shots", spy("film", projects_router.plan_shots))
    monkeypatch.setattr(projects_router, "revise", spy("director", projects_router.revise))
    await start(client, "vlog", character=bea["slug"], idea="a bake sale", shots=3)
    await start(
        client,
        "faceless",
        topic="bread",
        theme="creature-documentary",
        style="wildlife-documentary",
        minutes=0.5,
        character=bea["slug"],
    )
    await drain(in_memory_queue)  # both stop at their script review
    assert seen["vlog"]["name"] == "Bea Lines" and "Personality: warm, nosy" in seen["vlog"]["profile"]
    assert (
        seen["faceless"]["star"] == "Bea Lines"
        and '"Butter fixes everything."' in seen["faceless"]["star_profile"]
    )

    # a cast member wearing a look is matched on the handle alone, by the planner and by the director chat
    worn = [f"@{bea['slug']}:apron"]
    film = await client.post(
        "/projects", json={"title": "Bakery", "document": {"script": "Bea opens the shop.", "cast": worn}}
    )
    r = await client.post(f"/projects/{film.json()['id']}/plan", json={"cast": worn})
    assert r.status_code == 200, r.text
    ((slug, desc),) = seen["film"]["cast"]
    assert slug == bea["slug"] and desc.startswith("Bea Lines. a baker\nPersonality: warm, nosy")
    assert r.json()["scenes"][0]["shots"][0]["action"].startswith(f"@{bea['slug']} ")
    r = await client.post(f"/projects/{film.json()['id']}/director", json={"message": "Make shot 1 tighter"})
    assert r.status_code == 200, r.text
    assert seen["director"]["cast"] == seen["film"]["cast"]


# ---------------------------------------------------------------- the likeness check


async def test_a_take_is_checked_against_the_character_and_the_answer_kept(
    client, in_memory_queue, monkeypatch
):
    lia = await make_element(client, "Lia Likeness", description="a woman with long auburn hair", images=3)
    main = ids(lia)
    asked: list[dict] = []
    real = elements_router.check_likeness

    async def spy(*args, **kwargs):
        asked.append(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(elements_router, "check_likeness", spy)
    check = f"/elements/{lia['id']}/check"

    plain = await take(client, in_memory_queue, f"{lia['handle']} waves from a balcony")
    r = await client.post(check, json={"asset_id": plain})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["verdict"], body["differences"], body["mock"], body["cost_usd"]) == ("match", [], True, 0)
    assert asked[-1]["references"] == [uuid.UUID(a) for a in main[:2]]  # the two a mention attaches
    assert asked[-1]["prompt"] == f"{lia['handle']} waves from a balcony" and asked[-1]["look"] == ""

    odd = await take(client, in_memory_queue, f"{lia['handle']} waves [[mock:inconsistent]]")
    body = (await client.post(check, json={"asset_id": odd})).json()
    assert body["verdict"] == "different" and {d["aspect"] for d in body["differences"]} == {"face", "hair"}
    kept = (await client.get(f"/assets/{odd}")).json()["metadata"]
    assert kept["consistency"][lia["id"]]["verdict"] == "different"
    assert (
        kept["consistency"][lia["id"]]["model"].endswith("(mock)")
        and kept["consistency"][lia["id"]]["checked_at"]
    )
    assert kept["consistency"][lia["id"]]["look"] is None and kept["prompt"]  # the take's own metadata stays
    assert (await client.get(f"/assets/{plain}")).json()["metadata"]["consistency"][lia["id"]][
        "verdict"
    ] == "match"

    # wearing a look: compared with the look's pictures
    await client.put(
        f"/elements/{lia['id']}/looks/winter", json={"name": "Winter", "description": "in a red coat"}
    )
    coat = await upload(client, (200, 10, 10))
    await client.post(f"/elements/{lia['id']}/images", json=[{"asset_id": coat, "look": "winter"}])
    worn = await take(client, in_memory_queue, f"{lia['handle']}:winter in the snow")
    r = await client.post(check, json={"asset_id": worn})
    assert r.status_code == 200, r.text
    assert asked[-1]["references"] == [uuid.UUID(coat)] and asked[-1]["look"] == "Winter, in a red coat"
    assert (await client.get(f"/assets/{worn}")).json()["metadata"]["consistency"][lia["id"]][
        "look"
    ] == "winter"

    # a second character's answer sits beside the first, compared with his one main picture and never with his
    # expressions grid; a picture of hers is never compared with itself
    kai = await make_element(client, "Kai Likeness", images=1)
    grid = await upload(client, (60, 160, 60))
    await client.post(f"/elements/{kai['id']}/images", json=[{"asset_id": grid, "view": "expressions"}])
    assert (await client.post(f"/elements/{kai['id']}/check", json={"asset_id": odd})).status_code == 200
    assert asked[-1]["references"] == [uuid.UUID(a) for a in ids(kai)]
    assert set((await client.get(f"/assets/{odd}")).json()["metadata"]["consistency"]) == {
        lia["id"],
        kai["id"],
    }
    r = await client.post(check, json={"asset_id": main[0]})
    assert r.status_code == 200 and asked[-1]["references"] == [uuid.UUID(a) for a in main[1:3]]


async def test_only_a_characters_pictures_or_clips_can_be_checked(client):
    nell = await make_element(client, "Nell Nopics", images=0)
    picture = await upload(client, (30, 30, 30))
    r = await client.post(f"/elements/{nell['id']}/check", json={"asset_id": picture})
    assert r.status_code == 422 and "no pictures to compare with" in r.text
    place = await make_element(client, "Check Harbour", kind="location")
    assert (
        await client.post(f"/elements/{place['id']}/check", json={"asset_id": picture})
    ).status_code == 422
    ana = await make_element(client, "Ana Checks")
    r = await client.post(f"/elements/{ana['id']}/check", json={"asset_id": str(uuid.uuid4())})
    assert r.status_code == 422 and "not found" in r.text
    line = Path(__file__).resolve().parents[4] / "fixtures" / "audio" / "line.mp3"
    sound = (
        await client.post("/uploads", files={"file": ("line.mp3", line.read_bytes(), "audio/mpeg")})
    ).json()
    r = await client.post(f"/elements/{ana['id']}/check", json={"asset_id": sound["id"]})
    assert r.status_code == 422 and "not a picture or a clip" in r.text


async def test_live_the_references_go_first_and_the_take_last(client, monkeypatch):
    answer = Likeness(
        verdict="close",
        summary="Her hair is shorter.",
        differences=[Difference(aspect="hair", detail="shoulder length, not waist length")],
    )
    messages = FakeMessages(
        SimpleNamespace(
            model="claude-sonnet-5-5",
            stop_reason="end_turn",
            stop_details=None,
            parsed_output=answer,
            usage=SimpleNamespace(
                input_tokens=7000, output_tokens=150, cache_read_input_tokens=0, cache_creation_input_tokens=0
            ),
            _request_id="req_test",
        )
    )
    monkeypatch.setattr(get_settings(), "llm_mode", "live")
    monkeypatch.setattr(
        llm_client, "get_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages))
    )
    ivy = await make_element(client, "Ivy Live", images=2)
    picture = await upload(client, (120, 40, 200))
    r = await client.post(f"/elements/{ivy['id']}/check", json={"asset_id": picture})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["verdict"], body["mock"], body["model"]) == ("close", False, "claude-sonnet-5-5") and body[
        "cost_usd"
    ] > 0
    (call,) = messages.calls
    content = call["messages"][0]["content"]
    assert [b["type"] for b in content] == [
        "image",
        "image",
        "image",
        "text",
    ]  # two references, then the take
    assert "The first 2 pictures are the references" in content[-1]["text"]
    assert any(row.basis["purpose"] == "character.check" for row in await ledger())
    kept = (await client.get(f"/assets/{picture}")).json()["metadata"]["consistency"][ivy["id"]]
    assert kept["verdict"] == "close" and kept["model"] == "claude-sonnet-5-5"


# ---------------------------------------------------------------- quick actions


async def test_a_character_becomes_a_rigged_3d_figure_in_the_library(client, in_memory_queue):
    rex = await make_element(client, "Rex Figure", description="a stocky man in a green parka", images=2)
    main = ids(rex)
    est = await client.post(
        "/pipelines/estimate", json={"recipe": "character_figure", "input": {"character": rex["slug"]}}
    )
    assert est.status_code == 200 and est.json()["generations"] == 3, est.text
    run = await start(client, "character_figure", character=rex["slug"])
    assert run["input"]["element_id"] == rex["id"] and run["input"]["rig"] is True
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    assert set(steps) == {"pose", "model", "rig", "keep"}
    async with session_scope() as s:
        pose = await s.get(Generation, uuid.UUID(steps["pose"]["generation_id"]))
        model = await s.get(Generation, uuid.UUID(steps["model"]["generation_id"]))
        rig = await s.get(Generation, uuid.UUID(steps["rig"]["generation_id"]))
    assert pose and model and rig
    assert [m["asset_id"] for m in pose.request["media"]] == main  # drawn from his main pictures
    assert [(m["role"], m["asset_id"]) for m in model.request["media"]] == [
        ("input_image", steps["pose"]["output"]["asset_id"])
    ]
    assert rig.request["media"][0]["asset_id"] == steps["model"]["output"]["asset_id"]
    out = done["output"]
    assert (
        out["rigged"]
        and out["asset_id"] == steps["rig"]["output"]["asset_id"]
        and out["element_id"] == rex["id"]
    )
    for glb in [out["model_asset_id"], *steps["rig"]["output"]["asset_ids"]]:
        asset = (await client.get(f"/assets/{glb}")).json()
        assert asset["kind"] == "model3d" and asset["metadata"]["element_id"] == rex["id"], asset


async def test_a_creature_figure_from_its_own_picture_is_left_unrigged(client, in_memory_queue):
    moss = await make_element(client, "Moss Figure", description="a mossy forest spirit", images=2)
    await client.patch(f"/elements/{moss['id']}", json={"attributes": {"traits": {"presentation": CREATURE}}})
    run = await start(client, "character_figure", character=moss["slug"], picture_asset_id=ids(moss)[1])
    assert run["input"]["rig"] is False
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    assert {s["key"] for s in done["steps"]} == {"model", "keep"}
    assert done["output"]["rigged"] is False

    # only its own main pictures: not someone else's, not a look's
    stranger = await upload(client, (5, 5, 5))
    await client.put(
        f"/elements/{moss['id']}/looks/autumn", json={"name": "Autumn", "description": "red leaves"}
    )
    leaves = await upload(client, (150, 60, 10))
    await client.post(f"/elements/{moss['id']}/images", json=[{"asset_id": leaves, "look": "autumn"}])
    for picture in (stranger, leaves):
        r = await client.post(
            "/pipelines",
            json={
                "recipe": "character_figure",
                "input": {"character": moss["slug"], "picture_asset_id": picture},
            },
        )
        assert r.status_code == 422 and "isn't one of" in r.text, r.text
    r = await client.post(
        "/pipelines", json={"recipe": "character_figure", "input": {"character": "nobody-here"}}
    )
    assert r.status_code == 422 and "no saved character called @nobody-here" in r.text


async def test_a_saved_character_says_hello_in_their_own_voice(client, in_memory_queue):
    hal = await make_element(client, "Hal Hello", images=2)
    main = ids(hal)
    lily = {"voice_id": "pFZP5JQG7iQjIQuC4Bku"}
    assert (
        await client.patch(
            f"/elements/{hal['id']}", json={"voice": {"model_id": "eleven-v4", "params": lily}}
        )
    ).status_code == 200
    # he speaks from a main picture that shows him once: not his reference sheet, even marked primary, and not the
    # first of a look's pictures
    pictures = f"/elements/{hal['id']}/images"
    r = await client.patch(f"{pictures}/{main[0]}", json={"view": "sheet", "primary": True})
    assert r.status_code == 200, r.text
    await client.put(f"/elements/{hal['id']}/looks/suit", json={"name": "Suit", "description": "a grey suit"})
    suit = await upload(client, (90, 90, 90))
    await client.post(pictures, json=[{"asset_id": suit, "look": "suit"}])
    assert (await client.patch(f"{pictures}/{suit}", json={"primary": True})).status_code == 200
    rendered = await client.post(
        "/templates/talking-photo/render",
        json={"fields": {"script": "Hi, I'm Hal."}, "media": {"character": hal["slug"]}},
    )
    assert rendered.status_code == 200, rendered.text
    est = await client.post("/pipelines/estimate", json=rendered.json())
    assert est.status_code == 200 and est.json()["generations"] == 2, est.text

    run = await start(client, "talking_shot", **rendered.json()["input"])
    assert run["input"]["image_asset_id"] == main[1] and run["input"]["voice"] == lily
    assert run["input"]["resolved_for"] == hal["slug"]
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]
    steps = {s["key"]: s for s in done["steps"]}
    async with session_scope() as s:
        voice = await s.get(Generation, uuid.UUID(steps["voice"]["generation_id"]))
        sync = await s.get(Generation, uuid.UUID(steps["sync[1:kling-avatar-2]"]["generation_id"]))
    assert voice and voice.request["params"]["voice_id"] == lily["voice_id"]
    assert sync and {m["role"]: m["asset_id"] for m in sync.request["media"]}["character_image"] == main[1]

    # the stored input (the character, its picture and voice) validates again when the run is revised, also once he's
    # renamed and his old handle names nobody
    assert (await client.patch(f"/elements/{hal['id']}", json={"name": "Hal Renamed"})).status_code == 200
    r = await client.post(
        f"/pipelines/{run['id']}/revise", json={"input": {**done["input"], "script": "Bye now."}}
    )
    assert r.status_code == 200, r.text
    await drain(in_memory_queue)
    assert (await get(client, run["id"]))["status"] == "succeeded"

    # naming someone else takes their picture and voice, not the ones filled in for him
    kai = await make_element(client, "Kai Hello", images=1)
    david = {"voice_id": "BNgbHR0DNeZixGQVzloa"}
    await client.patch(f"/elements/{kai['id']}", json={"voice": {"model_id": "eleven-v4", "params": david}})
    stored = (await get(client, run["id"]))["input"]
    r = await client.post(
        f"/pipelines/{run['id']}/revise", json={"input": {**stored, "character": kai["slug"]}}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["input"]["image_asset_id"], r.json()["input"]["voice"]) == (ids(kai)[0], david)
    await drain(in_memory_queue)
    assert (await get(client, run["id"]))["status"] == "succeeded"

    bare = await make_element(client, "No Face Hello", images=0)
    for handle, why in [
        ("nobody-here", "no saved character called @nobody-here"),
        (bare["slug"], "has no pictures"),
    ]:
        r = await client.post(
            "/pipelines",
            json={
                "recipe": "talking_shot",
                "input": {"character": handle, "script": "Hi", "models": [{"model_id": "kling-avatar-2"}]},
            },
        )
        assert r.status_code == 422 and why in r.text, r.text
