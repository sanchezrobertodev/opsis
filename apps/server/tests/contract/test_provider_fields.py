"""Provider payload contracts, verified 2026-10-06 against each provider's published schema.

Sources: fal per-endpoint OpenAPI (https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=...),
Replicate model schema (bytedance/seedance-2.5), Runway OpenAPI (version 2024-11-06), google-genai 2.28.
If a provider changes a field, update the registry YAML and the expectation here together.
"""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.validate import MediaRef, normalize

REG = load_registry(get_settings().resolve_registry_dir())
IMG = "https://example.test/a.png"


def payload(model_id: str, route_id: str, mode: str, params: dict | None = None, media: dict | None = None,
            prompt: str = "a test shot", negative: str | None = None):
    spec = REG.model(model_id)
    refs = [MediaRef(role=r, asset_id=f"{r}-{i}", kind="video" if "video" in r else "image")
            for r, n in (media or {}).items() for i in range(n)]
    req = normalize(spec, mode, prompt, params or {}, refs, negative)
    staged = {}
    for m in req.media:
        staged.setdefault(m.role, []).append(IMG)
    return build_payload(spec, spec.route(route_id), req, staged)


def test_veo_fal_first_last_frame_uses_frame_url_fields():
    endpoint, p, _ = payload("veo-3.1", "veo-3.1@fal", "flf2v", {"duration": 8},
                             {"start_image": 1, "end_image": 1})
    assert endpoint == "fal-ai/veo3.1/first-last-frame-to-video"
    assert p["first_frame_url"] == IMG and p["last_frame_url"] == IMG and "image_url" not in p
    assert p["duration"] == "8s"


def test_veo_fal_reference_has_no_seed_or_negative_and_is_8s():
    endpoint, p, _ = payload("veo-3.1", "veo-3.1@fal", "reference", {"duration": 8}, {"image_references": 2},
                             negative="blurry")
    assert endpoint == "fal-ai/veo3.1/reference-to-video"
    assert p["image_urls"] == [IMG, IMG]
    assert p["duration"] == "8s" and "seed" not in p and "negative_prompt" not in p


def test_veo_fal_image_to_video():
    _, p, _ = payload("veo-3.1", "veo-3.1@fal", "i2v", {"duration": 4}, {"start_image": 1})
    assert p["image_url"] == IMG and p["duration"] == "4s" and isinstance(p["seed"], int)


def test_veo_gemini_drops_unsupported_sdk_fields():
    _, p, _ = payload("veo-3.1", "veo-3.1@google", "t2v", {"duration": 8})
    assert p["duration_seconds"] == 8 and p["person_generation"] == "allow_all"
    assert "seed" not in p and "generate_audio" not in p
    _, p, _ = payload("veo-3.1", "veo-3.1@google", "i2v", {"duration": 8}, {"start_image": 1})
    assert p["person_generation"] == "allow_adult" and p["image"] == IMG


def test_veo_reference_mode_requires_8_seconds():
    with pytest.raises(Exception, match="8-second"):
        payload("veo-3.1", "veo-3.1@fal", "reference", {"duration": 4}, {"image_references": 1})


def test_kling_fal_uses_start_and_end_image_url():
    endpoint, p, _ = payload("kling-3.0", "kling-3.0@fal", "i2v", {"duration": 5}, {"start_image": 1, "end_image": 1})
    assert endpoint == "fal-ai/kling-video/v3/pro/image-to-video"
    assert p["start_image_url"] == IMG and p["end_image_url"] == IMG
    assert p["duration"] == "5" and "seed" not in p and "resolution" not in p


def test_seedance_fal_fields_and_seed_only_on_reference():
    _, p, _ = payload("seedance-2.5", "seedance-2.5@fal", "i2v", {"duration": 4}, {"start_image": 1, "end_image": 1})
    assert p["image_url"] == IMG and p["end_image_url"] == IMG and p["duration"] == "4" and "seed" not in p
    _, p, _ = payload("seedance-2.5", "seedance-2.5@fal", "omni_reference", {"duration": 4},
                      {"image_references": 2, "video_references": 1})
    assert p["image_urls"] == [IMG, IMG] and p["video_urls"] == [IMG] and p["task"] == "reference"
    assert "seed" in p


def test_seedance_replicate_frames_need_adaptive_aspect():
    endpoint, p, _ = payload("seedance-2.5", "seedance-2.5@replicate", "i2v", {"duration": 4},
                             {"start_image": 1, "end_image": 1})
    assert endpoint == "bytedance/seedance-2.5"
    assert p["image"] == IMG and p["last_frame_image"] == IMG and p["aspect_ratio"] == "adaptive"
    assert p["duration"] == 4  # Replicate takes an integer


def test_nano_banana_fal_resolution_is_uppercase():
    _, p, _ = payload("nano-banana-2", "nano-banana-2@fal", "t2i", {"resolution": "2k"})
    assert p["resolution"] == "2K"


def test_runway_gen45_payload():
    endpoint, p, _ = payload("runway-gen-4.5", "runway-gen-4.5@runway", "i2v", {"duration": 6, "aspect_ratio": "9:16"},
                             {"start_image": 1})
    assert endpoint == "image_to_video"
    assert p == {"promptText": "a test shot", "promptImage": IMG, "model": "gen4.5", "duration": 6,
                 "ratio": "720:1280", "seed": p["seed"]}


def test_runway_aleph2_payload_and_input_length_pricing():
    from studio.registry.pricing import estimate

    endpoint, p, _ = payload("runway-aleph-2", "runway-aleph-2@runway", "edit", {}, {"input_video": 1})
    assert endpoint == "video_to_video" and p["model"] == "aleph2" and p["videoUri"] == IMG
    route = REG.model("runway-aleph-2").route("runway-aleph-2@runway")
    clip = [MediaRef("input_video", "v", "video", duration_ms=10_000)]
    assert float(estimate(route.pricing, {}, "x", clip).usd) == pytest.approx(2.80)
    assert float(estimate(route.pricing, {}, "x", [MediaRef("input_video", "v", "video", duration_ms=500)]).usd) == pytest.approx(0.56)


def test_gemini_veo_route_retires_on_schedule():
    route = REG.model("veo-3.1").route("veo-3.1@google")
    assert route.sunset is not None and route.sunset.isoformat() == "2026-10-22"
