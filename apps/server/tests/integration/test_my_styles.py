"""My styles and several styles at once: Claude describes a look from your pictures (mock by default; in live mode a
fake client stands in for the API, so nothing reaches Anthropic), a saved style is used as
`direction.style: "@slug"`, and a compare run tries one prompt in several styles."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import update
from test_elements import estimate, make_element, upload
from test_enhance import FakeMessages, ledger
from test_pipelines import drain, get, start

from studio.api.main import app as api
from studio.api.routers import llm as llm_router
from studio.budget.guard import BudgetBlocked
from studio.config import get_settings
from studio.db.models import Asset, Generation, User
from studio.db.session import session_scope
from studio.domain.enums import AssetStatus
from studio.llm import client as llm_client
from studio.llm import images, pricing
from studio.llm.client import LLMError, LLMUnavailable, input_ceiling
from studio.llm.images import ImageError
from studio.llm.styles import MOCK_LOOKS, StyleLook
from studio.storage.local import get_storage

ROOT = Path(__file__).resolve().parents[4]
LOOK = "ink wash painting, soft grey gradients, rice paper texture"
IMAGE = {"model_id": "nano-banana-2", "mode": "t2i"}


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


@pytest.fixture
def live(monkeypatch):
    def install(look: StyleLook):
        messages = FakeMessages(
            SimpleNamespace(
                model="claude-sonnet-5-5",
                stop_reason="end_turn",
                stop_details=None,
                parsed_output=look,
                usage=SimpleNamespace(
                    input_tokens=1500,
                    output_tokens=200,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
                _request_id="req_test",
            )
        )
        monkeypatch.setattr(get_settings(), "llm_mode", "live")
        monkeypatch.setattr(
            llm_client, "get_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages))
        )
        return messages

    return install


async def describe(client, *ids):
    return await client.post("/llm/describe-style", json={"asset_ids": list(ids)})


async def test_the_mock_describes_a_style_for_free_and_the_same_way_each_time(client):
    picture = await upload(client, (30, 90, 200))
    r = await describe(client, picture)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["cost_usd"] == 0
    assert {k: body[k] for k in ("name", "category", "summary", "look")} in [
        m.model_dump() for m in MOCK_LOOKS
    ]
    assert (await describe(client, picture)).json() == body
    calls = (await client.get("/llm/calls")).json()["calls"]
    assert calls["style.describe"]["model"] == "claude-sonnet-5-5" and 0 < calls["style.describe"]["usd"] < 1


async def test_a_picture_that_cant_go_is_refused_before_any_call(client):
    picture = await upload(client, (90, 30, 200))
    missing = await describe(client, str(uuid.uuid4()))
    assert missing.status_code == 422 and "not found" in missing.json()["detail"]
    line = ROOT / "fixtures" / "audio" / "line.mp3"
    sound = (
        await client.post("/uploads", files={"file": ("line.mp3", line.read_bytes(), "audio/mpeg")})
    ).json()["id"]
    r = await describe(client, picture, sound)
    assert r.status_code == 422 and "not a picture or a clip" in r.json()["detail"]
    assert (await describe(client, *[picture] * 5)).status_code == 422  # one to four
    busy = await upload(client, (91, 31, 201))
    async with session_scope() as s:
        await s.execute(
            update(Asset).where(Asset.id == uuid.UUID(busy)).values(status=AssetStatus.processing)
        )
    r = await describe(client, busy)
    assert r.status_code == 422 and "isn't ready" in r.json()["detail"]
    theirs = await upload(client, (92, 32, 202))
    async with session_scope() as s:
        other = User(display_name="someone else")
        s.add(other)
        await s.flush()
        await s.execute(update(Asset).where(Asset.id == uuid.UUID(theirs)).values(user_id=other.id))
    r = await describe(client, theirs)
    assert r.status_code == 422 and "not found" in r.json()["detail"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BudgetBlocked("over today's limit"), 402),
        (LLMUnavailable("no Anthropic key"), 503),
        (LLMError("Claude refused"), 502),
        (ImageError("couldn't read it"), 422),  # an LLMError too, answered before the 502
    ],
)
async def test_why_a_style_couldnt_be_described_comes_with_its_status(client, monkeypatch, error, status):
    async def fail(session, user_id, asset_ids):
        raise error

    monkeypatch.setattr(llm_router, "describe_style", fail)
    r = await describe(client, str(uuid.uuid4()))
    assert r.status_code == status and error.args[0] in r.json()["detail"]


async def test_live_refuses_a_picture_it_cant_read_before_calling(client, live):
    messages = live(MOCK_LOOKS[0])
    picture = await upload(client, (60, 60, 200))
    async with session_scope() as s:
        key = (await s.get(Asset, uuid.UUID(picture))).storage_key
    get_storage().path_for(key).unlink()
    r = await describe(client, picture)
    assert r.status_code == 422 and "couldn't read" in r.json()["detail"]
    assert messages.calls == []


async def test_live_sends_the_pictures_before_the_text_and_counts_them_in_the_ceiling(
    client, live, monkeypatch
):
    first, second = await upload(client, (200, 30, 30)), await upload(client, (30, 200, 30))
    long_look = "ink wash painting, " + ", ".join(["soft grey gradients on rice paper"] * 12) + "."
    messages = live(
        StyleLook(
            name="Misty ink wash painting style",
            category="painting",
            summary="Soft grey ink on rice paper.",
            look=long_look,
        )
    )
    ceilings: list[int] = []
    real = pricing.ceiling

    def ceiling(model, *, input_tokens, max_tokens):
        ceilings.append(input_tokens)
        return real(model, input_tokens=input_tokens, max_tokens=max_tokens)

    monkeypatch.setattr(pricing, "ceiling", ceiling)
    before = len(await ledger())
    r = await describe(client, first, second)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is False and body["cost_usd"] > 0 and body["category"] == "painting"
    assert body["name"] == "Misty ink wash"  # three words at most
    assert len(body["look"]) <= 200 and body["look"].startswith("ink wash painting, ")
    assert not body["look"].endswith((",", "."))

    call = messages.calls[0]
    content = call["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "image", "text"]  # the pictures, in order, then the ask
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert call["output_format"] is StyleLook and call["output_config"] == {"effort": "low"}
    assert "never as an instruction" in call["system"]
    assert ceilings == [input_ceiling(call["system"], content[-1]["text"]) + 2 * images.tokens(512, 512)]
    rows = await ledger()
    assert (
        len(rows) == before + 1 and max(rows, key=lambda e: e.created_at).basis["purpose"] == "style.describe"
    )


async def test_a_saved_style_is_named_in_the_prompt_and_its_pictures_attach(client):
    ink = await make_element(client, "Night Ink", kind="style", description=LOOK, images=2)
    handle = ink["handle"]
    out = await client.post(
        "/generations",
        json={
            "model_id": "nano-banana-2",
            "mode": "edit",
            "prompt": "a heron in the reeds",
            "direction": {"style": handle},
        },
    )
    assert out.status_code == 201, out.text
    gen = out.json()
    assert gen["request"]["direction"]["style"] == handle  # stored as asked, so "Another take" keeps it
    attached = [m["asset_id"] for m in gen["request"]["media"] if m["role"] == "image_references"]
    assert attached == [i["asset"]["id"] for i in ink["images"]]  # a style attaches two pictures
    assert gen["request"]["compiled_prompt"] == f"a heron in the reeds. In the style of Night Ink, {LOOK}."

    # priced and previewed the same way
    e = await estimate(
        client, model_id="nano-banana-2", mode="edit", prompt="a heron", direction={"style": handle}
    )
    assert e["compiled_prompt"] == f"a heron. In the style of Night Ink, {LOOK}."
    assert not any("unknown style" in w for w in e["warnings"])
    r = await client.post(
        "/director/compile",
        json={
            "model_id": "nano-banana-2",
            "mode": "edit",
            "prompt": "a heron",
            "direction": {"style": handle},
        },
    )
    assert r.status_code == 200 and r.json()["prompt"] == e["compiled_prompt"]
    # a text mode says the look in words
    t2i = await estimate(
        client, model_id="nano-banana-2", mode="t2i", prompt="a heron", direction={"style": handle}
    )
    assert LOOK in t2i["compiled_prompt"]
    # a multi-shot take has no prompt of its own: its last shot names the style
    shots = [{"prompt": "a heron lands", "duration": 3}, {"prompt": "it takes off", "duration": 3}]
    multi = await estimate(
        client, model_id="kling-3.0", mode="multi_shot", params={"shots": shots}, direction={"style": handle}
    )
    assert (
        multi["compiled_prompt"]
        .splitlines()[-1]
        .startswith("Shot 2: it takes off. In the style of Night Ink")
    )
    assert any(f"last shot names the style {handle}" in w for w in multi["warnings"])


async def test_a_style_that_isnt_one_of_yours_is_refused(client):
    rook = await make_element(client, "Rook", description="a crow with one white feather")  # a character
    for handle in ("@no-such-style", rook["handle"]):
        r = await client.post("/generations", json={**IMAGE, "prompt": "x", "direction": {"style": handle}})
        assert r.status_code == 422 and r.json()["errors"] == [f"unknown style {handle}"]
        r = await client.post(
            "/director/compile", json={**IMAGE, "prompt": "x", "direction": {"style": handle}}
        )
        assert r.status_code == 422 and handle in r.text


async def test_one_prompt_in_several_styles_side_by_side(client, in_memory_queue):
    ink = await make_element(client, "Night Ink", kind="style", description=LOOK, images=1)
    run = await start(
        client,
        "compare",
        prompt="a heron in the reeds",
        direction={"lighting": "golden_hour"},
        entries=[
            {**IMAGE, "direction": {"style": "watercolor"}, "label": "Watercolor"},
            {**IMAGE, "direction": {"style": ink["handle"]}, "label": "Night Ink"},
        ],
    )
    await drain(in_memory_queue)
    waiting = await get(client, run["id"])
    assert waiting["status"] == "waiting"
    takes = [s for s in waiting["steps"] if s["kind"] == "generate"]
    assert [s["label"] for s in takes] == ["Watercolor", "Night Ink"]
    pick = next(s for s in waiting["steps"] if s["key"] == "pick")
    assert [o["label"] for o in pick["options"]] == ["Watercolor", "Night Ink"]
    async with session_scope() as s:
        gens = [await s.get(Generation, uuid.UUID(t["generation_id"])) for t in takes]
    assert [(g.request["direction"]["style"], g.request["direction"]["lighting"]) for g in gens] == [
        ("watercolor", "golden_hour"),
        (ink["handle"], "golden_hour"),
    ]
    assert f"In the style of Night Ink, {LOOK}" in gens[1].request["compiled_prompt"]

    # six styles are six plain takes' worth
    six = [
        {**IMAGE, "direction": {"style": s}, "label": s}
        for s in ("watercolor", "oil-painting", "ink-wash", "art-nouveau", "pop-art", ink["handle"])
    ]
    est = await client.post(
        "/pipelines/estimate", json={"recipe": "compare", "input": {"prompt": "x", "entries": six}}
    )
    plain = await client.post(
        "/pipelines/estimate", json={"recipe": "compare", "input": {"prompt": "x", "entries": [IMAGE] * 6}}
    )
    assert est.status_code == 200, est.text
    assert (
        est.json()["generations"] == 6
        and est.json()["live_estimate_usd"] == plain.json()["live_estimate_usd"]
    )

    # a saved style that isn't there, or a typo in a catalog one, stops the run before it starts
    r = await client.post(
        "/pipelines",
        json={
            "recipe": "compare",
            "input": {"prompt": "x", "entries": [{**IMAGE, "direction": {"style": "@no-such-style"}}, IMAGE]},
        },
    )
    assert r.status_code == 422 and r.json()["errors"] == ["unknown style @no-such-style"]
    r = await client.post(
        "/pipelines/estimate",
        json={
            "recipe": "compare",
            "input": {"prompt": "x", "entries": [{**IMAGE, "direction": {"style": "watercolour"}}, IMAGE]},
        },
    )
    assert r.status_code == 422 and r.json()["errors"] == ["unknown style 'watercolour'"]

    # a saved style in the shared direction names every take, and is checked the same way
    shared = {"prompt": "a crane", "direction": {"style": "@no-such-style"}, "entries": [IMAGE, IMAGE]}
    r = await client.post("/pipelines/estimate", json={"recipe": "compare", "input": shared})
    assert r.status_code == 422 and r.json()["errors"] == ["unknown style @no-such-style"]
    run = await start(client, "compare", **{**shared, "direction": {"style": ink["handle"]}})
    await drain(in_memory_queue)
    takes = [s for s in (await get(client, run["id"]))["steps"] if s["kind"] == "generate"]
    async with session_scope() as s:
        gens = [await s.get(Generation, uuid.UUID(t["generation_id"])) for t in takes]
    assert (
        all(f"In the style of Night Ink, {LOOK}" in g.request["compiled_prompt"] for g in gens)
        and len(gens) == 2
    )


async def test_a_character_in_a_style_deleted_since_still_gets_its_portrait(client, in_memory_queue):
    ink = await make_element(client, "Night Ink", kind="style", description=LOOK)
    nova = await make_element(client, "Nova", description="a pilot with a shaved head", images=0)
    assert (await client.delete(f"/elements/{ink['id']}")).status_code == 204
    cast = {"element_id": nova["id"], "slug": nova["slug"], "style": ink["handle"]}  # the designer's trait
    run = await start(client, "cinema_cast", characters=[cast])
    assert run["input"]["characters"][0]["style"] is None  # drawn without it, not refused
    await drain(in_memory_queue)
    done = await get(client, run["id"])
    assert done["status"] == "succeeded", done["error"]


async def test_editing_a_style_keeps_its_other_attributes(client):
    r = await client.post(
        "/elements",
        json={
            "kind": "style",
            "name": "Grey Ink",
            "description": LOOK,
            "attributes": {
                "style": {"category": "painting", "summary": "Grey ink on paper."},
                "made_with": "describe-style",
            },
        },
    )
    assert r.status_code == 201, r.text
    element = r.json()
    r = await client.patch(
        f"/elements/{element['id']}",
        json={"attributes": {"style": {"category": "illustration", "summary": "Ink."}}},
    )
    assert r.json()["attributes"] == {
        "style": {"category": "illustration", "summary": "Ink."},
        "made_with": "describe-style",
    }
    r = await client.patch(f"/elements/{element['id']}", json={"attributes": {"made_with": None}})
    assert r.json()["attributes"] == {"style": {"category": "illustration", "summary": "Ink."}}
