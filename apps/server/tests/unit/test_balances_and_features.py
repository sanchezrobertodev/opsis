"""Provider balances (HTTP mocked, never a live call) and which studio feature a charge belongs to."""

from __future__ import annotations

import httpx
import pytest
import respx

from studio.budget import balances as b
from studio.budget.features import feature_of
from studio.config import get_settings
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())


@pytest.mark.parametrize(("recipe", "kind", "model", "mode", "feature"), [
    ("cinema_takes", None, "kling-3.0", "i2v", "cinema"),
    ("faceless", None, "eleven-v4", "tts", "faceless"),     # the recipe wins over the model's own tags
    ("compare", None, "veo-3.1", "t2v", "create_video"),
    ("compare", None, "nano-banana-2", "t2i", "create_image"),   # a compare from Create image
    ("compare", None, "topaz-video", "upscale", "edit"),         # or from Edit video
    ("sequence", None, "kling-3.0", "i2v", "create_video"),
    (None, "cinema", "sync-lipsync-3", "lipsync", "cinema"),  # a take of a film shot is the film's
    (None, None, "sync-lipsync-3", "lipsync", "lipsync"),
    (None, None, "kling-3.0-motion-control", "transfer", "motion"),
    (None, None, "sam-3-video", "track", "swap"),
    (None, None, "eleven-music", "music", "audio"),
    (None, None, "marble", "text_to_world", "worlds"),
    (None, None, "trellis-2", "image_to_3d", "jutsu3d"),
    (None, None, "wan-vace-depth", "depth", "jutsu3d"),
    (None, None, "nano-banana-2", "t2i", "create_image"),
    (None, None, "topaz-video", "upscale", "edit"),
    (None, None, "veo-3.1", "t2v", "create_video"),
    (None, None, None, None, "other"),
])
def test_a_charge_belongs_to_a_feature(recipe, kind, model, mode, feature):
    spec = REG.model(model) if model else None
    if spec is not None and spec.mode(mode) is None:  # the test names a mode by what it does; find the real id
        mode = next(m.id for m in spec.modes)
    assert feature_of(recipe=recipe, project_kind=kind, spec=spec, mode_id=mode) == feature


def test_every_op_in_the_registry_belongs_to_a_feature():
    from studio.budget.features import OPS

    known = set().union(*(tags for tags, _ in OPS))
    ops = {op for spec in REG.models.values() for mode in spec.modes for op in mode.ops}
    assert ops <= known, f"ops with no feature: {sorted(ops - known)}"


def test_claude_models_read_as_names():
    from studio.budget.features import claude_name

    assert claude_name("claude-opus-5-5") == "Claude Opus 5.5" and claude_name("claude-haiku-4-5") == "Claude Haiku 4.5"


@pytest.fixture
def keys(monkeypatch):
    s = get_settings()
    for attr, value in (("fal_key", "fal-secret"), ("runpod_api_key", "rp-secret"), ("runway_api_key", "rw-secret"),
                        ("elevenlabs_api_key", "el-secret"), ("anthropic_api_key", "sk-secret"),
                        ("gemini_api_key", ""), ("replicate_api_token", ""), ("worldlabs_api_key", ""),
                        ("reactor_api_key", ""), ("pika_api_key", "")):
        monkeypatch.setattr(s, attr, value)
    b.clear_cache()
    yield
    b.clear_cache()


@respx.mock
async def test_balances_from_the_providers_that_report_them(keys):
    fal = respx.get("https://api.fal.ai/v1/account/billing").respond(
        200, json={"username": "me", "credits": {"current_balance": 7.73, "currency": "USD"}})
    respx.post("https://api.runpod.io/graphql").respond(
        200, json={"data": {"myself": {"clientBalance": 16.16, "currentSpendPerHr": 0.013}}})
    respx.get("https://api.dev.runwayml.com/v1/organization").respond(200, json={"creditBalance": 862})
    respx.get("https://api.elevenlabs.io/v1/user/subscription").respond(
        200, json={"character_count": 704, "character_limit": 90000, "tier": "starter",
                   "next_character_count_reset_unix": 1791443781})
    got = {x.provider: x for x in await b.balances()}
    assert list(got) == ["fal", "runpod", "runway", "elevenlabs", "anthropic"]  # only providers with a key
    assert (got["fal"].amount, got["fal"].unit) == (7.73, "usd")
    assert got["runpod"].usd == 16.16 and "0.013 an hour" in got["runpod"].detail
    assert (got["runway"].amount, got["runway"].usd) == (862, 8.62)
    assert got["elevenlabs"].amount == 89296 and "starter plan" in got["elevenlabs"].detail
    assert got["anthropic"].status == "no_api" and got["anthropic"].link.startswith("https://")
    assert fal.calls.last.request.headers["authorization"] == "Key fal-secret"
    assert "secret" not in repr(list(got.values()))  # keys never come back

    await b.balances()  # within the minute: read from the cache
    assert fal.call_count == 1
    await b.balances(refresh=True)
    assert fal.call_count == 2


@respx.mock
async def test_one_provider_failing_hides_nothing_else(keys):
    respx.get("https://api.fal.ai/v1/account/billing").respond(403, json={"error": {"type": "authorization_error"}})
    respx.post("https://api.runpod.io/graphql").mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get("https://api.dev.runwayml.com/v1/organization").respond(500)
    respx.get("https://api.elevenlabs.io/v1/user/subscription").respond(200, json={"character_count": 1,
                                                                                    "character_limit": 10})
    got = {x.provider: x for x in await b.balances()}
    assert got["fal"].status == "unavailable" and "admin" in got["fal"].detail
    assert got["runpod"].status == "error" and "ConnectTimeout" in got["runpod"].detail
    assert got["runway"].status == "error" and "500" in got["runway"].detail
    assert got["elevenlabs"].status == "ok" and got["elevenlabs"].amount == 9
