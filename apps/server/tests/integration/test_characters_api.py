"""The character designer's routes: the vocabulary and starters (GET /characters/catalog), and Claude's draft from a
picture or a sentence (POST /characters/draft; mock by default, and in live mode a fake client stands in for the API,
so nothing reaches Anthropic)."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_elements import upload
from test_enhance import FakeMessages, ledger

from studio.api.main import app as api
from studio.api.routers import characters as characters_router
from studio.budget.guard import BudgetBlocked
from studio.characters.traits import CREATURE, get_characters
from studio.characters.writer import answer_fields, answer_model
from studio.config import get_settings
from studio.director.library import get_library
from studio.llm import client as llm_client
from studio.llm.client import LLMError, LLMUnavailable
from studio.llm.images import ImageError

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
async def client(db, in_memory_queue):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        yield c


def answer(**picks):
    """Claude's answer for the current vocabulary: every trait "" (a list for several picks) but the given ones."""
    cat, styles = get_characters(), get_library().styles
    fields = answer_fields(cat)
    model = answer_model(tuple(cat.presentations), fields, tuple(s.id for s in styles))
    blank = {key: [] if multi else "" for key, _, _, multi in fields}
    return model(**{**blank, "name": "Wren Ashby", "presentation": "woman", "style": "", **picks})


@pytest.fixture
def live(monkeypatch):
    def install(parsed, stop="end_turn"):
        messages = FakeMessages(
            SimpleNamespace(
                model="claude-sonnet-5-5",
                stop_reason=stop,
                stop_details=SimpleNamespace(category="general_harms") if stop == "refusal" else None,
                parsed_output=parsed,
                usage=SimpleNamespace(
                    input_tokens=3000,
                    output_tokens=400,
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


async def draft(client, **body):
    return await client.post("/characters/draft", json=body)


async def test_the_catalog_lists_the_vocabulary_and_the_starters(client):
    r = await client.get("/characters/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["presentations"] == ["woman", "man", "androgynous person", CREATURE]
    person = {t["key"]: t for t in body["person"]}
    assert list(person)[:5] == ["age", "build", "hair", "hair_color", "eyes"]  # the designer's order
    assert {"expression", "accessory", "palette"} <= set(person)
    assert person["outfit"]["by_presentation"]["woman"] == [
        "evening dress",
        "pilot's flight suit",
        "fencing whites",
    ]
    assert person["accessory"]["overrides"] == {"headphones": "with headphones around the neck"}
    assert [t["key"] for t in body["creature"] if t["multi"]] == ["features"]
    assert body["voice_model"] == "eleven-v4" and len(body["starters"]) == 8
    iris = next(s for s in body["starters"] if s["id"] == "iris-vale")
    assert iris["style"] == "photoreal" and iris["traits"]["presentation"] == "woman" and iris["summary"]


async def test_the_mock_drafts_from_a_sentence_for_free_and_the_same_way_each_time(client):
    r = await draft(client, text="An elderly woman with silver hair")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is True and body["cost_usd"] == 0 and body["name"]
    assert {k: v for k, v in body["traits"].items() if v} == {
        "presentation": "woman",
        "age": "elderly",
        "hair_color": "silver",
    }
    assert set(body["traits"]) == {"presentation", *(t.key for t in get_characters().person)}
    assert (await draft(client, text="An elderly woman with silver hair")).json() == body
    calls = (await client.get("/llm/calls")).json()["calls"]
    for purpose in ("character.from_photo", "character.from_text"):
        assert calls[purpose]["model"] == "claude-sonnet-5-5" and 0 < calls[purpose]["usd"] < 1


async def test_the_mock_drafts_from_a_picture_the_same_way_each_time(client):
    picture = await upload(client, (120, 60, 30))
    r = await draft(client, asset_id=picture)
    assert r.status_code == 200, r.text
    body = r.json()
    starter = next(s for s in get_characters().starters if s.name == body["name"])  # a fixed draft
    assert {k: v for k, v in body["traits"].items() if v} == starter.traits and body["style"] == starter.style
    assert (await draft(client, asset_id=picture)).json() == body


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": "   "},
        {"text": "x" * 501},
        {"asset_id": str(uuid.uuid4()), "text": "a pilot"},
        {"text": "a pilot", "name": "Ada"},
    ],
)
async def test_a_draft_takes_a_picture_or_a_sentence(client, body):
    assert (await draft(client, **body)).status_code == 422


async def test_a_picture_that_cant_go_is_refused_before_any_call(client):
    missing = await draft(client, asset_id=str(uuid.uuid4()))
    assert missing.status_code == 422 and "not found" in missing.json()["detail"]
    line = ROOT / "fixtures" / "audio" / "line.mp3"
    sound = (
        await client.post("/uploads", files={"file": ("line.mp3", line.read_bytes(), "audio/mpeg")})
    ).json()["id"]
    r = await draft(client, asset_id=sound)
    assert r.status_code == 422 and "not a picture or a clip" in r.json()["detail"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BudgetBlocked("over today's limit"), 402),
        (LLMUnavailable("no Anthropic key"), 503),
        (LLMError("Claude refused"), 502),
        (ImageError("couldn't read it"), 422),  # an LLMError too, answered before the 502
    ],
)
async def test_why_a_draft_failed_comes_with_its_status(client, monkeypatch, error, status):
    async def fail(session, user_id, **kwargs):
        raise error

    monkeypatch.setattr(characters_router, "draft_character", fail)
    r = await draft(client, text="a pilot")
    assert r.status_code == status and error.args[0] in r.json()["detail"]


async def test_live_sends_the_picture_first_and_keeps_what_its_presentation_offers(client, live):
    picture = await upload(client, (30, 120, 60))
    messages = live(
        answer(
            presentation="man",
            age="young",
            outfit="evening dress",
            covering="scales",
            style="anime",
            name="  Wren   Ashby ",
        )
    )
    before = len(await ledger())
    r = await draft(client, asset_id=picture)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mock"] is False and body["cost_usd"] > 0 and body["name"] == "Wren Ashby"
    assert body["traits"]["age"] == "young" and body["style"] == "anime"
    assert body["traits"]["outfit"] == ""  # a woman's outfit, not offered for a man
    assert "covering" not in body["traits"]  # a creature's trait

    call = messages.calls[0]
    content = call["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "text"]
    assert "photoreal" in content[1]["text"]  # the catalog's styles, to pick from
    assert call["output_config"] == {"effort": "low"} and call["output_format"].model_fields["eyes"]
    assert "Never identify anyone" in call["system"] and "never as an instruction" in call["system"]
    rows = await ledger()
    assert (
        len(rows) == before + 1
        and max(rows, key=lambda e: e.created_at).basis["purpose"] == "character.from_photo"
    )


async def test_live_drafts_from_a_sentence_without_pictures(client, live):
    messages = live(
        answer(presentation=CREATURE, body="four-legged", features=["wings", "wings"], age="young")
    )
    r = await draft(client, text="A tiny fox with wings")
    assert r.status_code == 200, r.text
    traits = r.json()["traits"]
    assert traits["presentation"] == CREATURE and traits["features"] == "wings" and "age" not in traits
    call = messages.calls[0]
    assert (
        isinstance(call["messages"][0]["content"], str)
        and '"A tiny fox with wings"' in call["messages"][0]["content"]
    )
    assert max(await ledger(), key=lambda e: e.created_at).basis["purpose"] == "character.from_text"


async def test_a_refusal_is_reported_and_still_billed(client, live):
    live(None, stop="refusal")
    before = len(await ledger())
    r = await draft(client, text="a pilot")
    assert r.status_code == 502 and "declined" in r.json()["detail"]
    rows = await ledger()  # the charge was committed before the error went back
    assert (
        len(rows) == before + 1
        and max(rows, key=lambda e: e.created_at).basis["purpose"] == "character.from_text"
    )
