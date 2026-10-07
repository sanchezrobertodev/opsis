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


# ---------------------------------------------------------------- P3: edit, finishing, motion transfer


def price(model_id: str, route_id: str, mode: str, params: dict, media: list) -> float:
    from studio.registry.pricing import estimate

    spec = REG.model(model_id)
    req = normalize(spec, mode, "x", params, media)
    return float(estimate(spec.route(route_id).pricing, req.params, "x", req.media, mode).usd)


def clip(role="input_video", seconds=10.0, fps=None):
    return MediaRef(role, f"{role}-0", "video", duration_ms=int(seconds * 1000), fps=fps)


def test_kling_o3_edit_tier_picks_the_endpoint_and_bills_the_clip():
    endpoint, p, _ = payload("kling-o3-edit", "kling-o3-edit@fal", "edit", {"tier": "pro"},
                             {"input_video": 1, "image_references": 2})
    assert endpoint == "fal-ai/kling-video/o3/pro/video-to-video/edit"
    assert p == {"prompt": "a test shot", "video_url": IMG, "image_urls": [IMG, IMG], "keep_audio": True}
    assert price("kling-o3-edit", "kling-o3-edit@fal", "edit", {"tier": "standard"}, [clip()]) == pytest.approx(1.26)


def test_kling_motion_control_caps_billing_by_orientation():
    endpoint, p, _ = payload("kling-3.0-motion-control", "kling-3.0-motion-control@fal", "transfer",
                             {"character_orientation": "image"}, {"character_image": 1, "driving_video": 1})
    assert endpoint == "fal-ai/kling-video/v3/standard/motion-control"
    assert p["image_url"] == IMG and p["video_url"] == IMG and p["character_orientation"] == "image" and "tier" not in p
    long_clip = [MediaRef("character_image", "c", "image"), clip("driving_video", 20)]
    args = ("kling-3.0-motion-control", "kling-3.0-motion-control@fal", "transfer")
    assert price(*args, {"character_orientation": "image"}, long_clip) == pytest.approx(10 * 0.126)
    assert price(*args, {"character_orientation": "video"}, long_clip) == pytest.approx(20 * 0.126)
    # Kling 2.6 on RunPod bills per started 3 s of the driving clip only
    old = [MediaRef("character_image", "c", "image"), clip("driving_video", 7)]
    assert price("kling-2.6-motion-control", "kling-2.6-motion-control@runpod", "transfer", {}, old) == pytest.approx(0.63)


def test_luma_modify_and_reframe():
    _, p, _ = payload("luma-ray-3.2", "luma-ray-3.2@fal", "modify", {"duration": 10, "edit_strength": "reimagine_2"},
                      {"input_video": 1})
    assert p["duration"] == "10s" and p["edit_strength"] == "reimagine_2" and p["video_url"] == IMG
    endpoint, p, _ = payload("luma-ray-3.2", "luma-ray-3.2@fal", "reframe", {"aspect_ratio": "9:16"}, {"input_video": 1})
    assert endpoint == "luma/agent/ray/v3.2/reframe" and p["aspect_ratio"] == "9:16" and "duration" not in p
    args = ("luma-ray-3.2", "luma-ray-3.2@fal")
    assert price(*args, "modify", {"resolution": "720p", "duration": 10}, [clip()]) == pytest.approx(2.16)
    assert price(*args, "reframe", {"resolution": "540p"}, [clip(seconds=7.2)]) == pytest.approx(8 * 0.06)


def test_seedance_edit_and_extend_are_tasks_with_a_video_list():
    _, p, _ = payload("seedance-2.5", "seedance-2.5@fal", "video_edit", {}, {"input_video": 1, "image_references": 1})
    assert p["task"] == "editing" and p["video_urls"] == [IMG] and p["image_urls"] == [IMG]
    assert p["duration"] == "auto" and p["aspect_ratio"] == "auto"
    _, p, _ = payload("seedance-2.5", "seedance-2.5@fal", "extend", {"duration": 6}, {"input_video": 1})
    assert p["task"] == "extension" and p["video_urls"] == [IMG] and p["duration"] == "6"
    args = ("seedance-2.5", "seedance-2.5@fal")
    assert price(*args, "video_edit", {"resolution": "720p"}, [clip()]) == pytest.approx(20 * 0.2838)
    assert price(*args, "extend", {"resolution": "720p", "duration": 5}, [clip()]) == pytest.approx(15 * 0.2838)
    assert price(*args, "t2v", {"resolution": "720p", "duration": 5}, []) == pytest.approx(5 * 0.473)


def test_flux_3_edit_and_extend():
    endpoint, p, _ = payload("flux-3-video", "flux-3-video@fal", "extend", {"duration": 8}, {"input_video": 1})
    assert endpoint == "blackforestlabs/flux-3/extend-video" and p["duration"] == "8" and p["aspect_ratio"] == "auto"
    assert price("flux-3-video", "flux-3-video@fal", "edit", {}, [clip(seconds=12)]) == pytest.approx(0.36)


def test_wan_animate_bills_per_input_frame():
    endpoint, p, _ = payload("wan-2.2-animate", "wan-2.2-animate@fal", "replace", {"steps": 12},
                             {"character_image": 1, "driving_video": 1})
    assert endpoint == "fal-ai/wan/v2.2-14b/animate/replace"
    assert p["num_inference_steps"] == 12 and p["image_url"] == IMG and p["video_url"] == IMG and "prompt" not in p
    media = [MediaRef("character_image", "c", "image"), clip("driving_video", 5, fps=60)]
    assert price("wan-2.2-animate", "wan-2.2-animate@fal", "move", {"resolution": "720p"}, media) == pytest.approx(1.50)


def test_finishing_models():
    _, p, _ = payload("topaz-video", "topaz-video@fal", "interpolate", {"interp_model": "Aion"}, {"input_video": 1})
    assert p == {"video_url": IMG, "target_fps": 60, "slowdown_factor": 1, "model": "Aion", "H264_output": True}
    _, p, _ = payload("topaz-video", "topaz-video@fal", "upscale_generative", {}, {"input_video": 1})
    assert p["model"] == "Starlight Precise 2.6" and p["upscale_factor"] == 2 and p["H264_output"] is True
    # 10 s at 30 fps -> 60 fps adds 300 frames
    assert price("topaz-video", "topaz-video@fal", "interpolate", {}, [clip(fps=30)]) == pytest.approx(0.30)
    assert price("bytedance-upscaler", "bytedance-upscaler@fal", "upscale", {"target_resolution": "4k", "target_fps": 60},
                 [clip()]) == pytest.approx(10 * 2 * 0.0288)
    _, p, _ = payload("bria-video-background", "bria-video-background@fal", "remove", {}, {"input_video": 1})
    assert p == {"video_url": IMG, "background_color": "Transparent", "preserve_audio": True,
                 "output_container_and_codec": "webm_vp9"}
    _, p, _ = payload("kling-video-to-audio", "kling-video-to-audio@fal", "add_sound", {}, {"input_video": 1},
                      prompt="rain on a tin roof")
    assert p["sound_effect_prompt"] == "rain on a tin roof" and p["background_music_prompt"].startswith("no music")


def test_runway_act_two_wraps_inputs_as_typed_objects():
    endpoint, p, _ = payload("runway-act-two", "runway-act-two@runway", "perform", {},
                             {"character_image": 1, "driving_video": 1})
    assert endpoint == "character_performance" and p["model"] == "act_two"
    assert p["character"] == {"type": "image", "uri": IMG} and p["reference"] == {"type": "video", "uri": IMG}
    assert p["bodyControl"] is True and p["expressionIntensity"] == 3 and p["ratio"] == "1280:720"


def test_runpod_public_endpoints():
    endpoint, p, _ = payload("pruna-p-video-edit", "pruna-p-video-edit@runpod", "edit", {"draft": True},
                             {"input_video": 1, "image_references": 2})
    assert endpoint == "p-video-edit" and p["video"] == IMG and p["images"] == [IMG, IMG] and p["draft"] is True
    endpoint, p, _ = payload("wan-2.2-14b", "wan-2.2-14b@runpod", "i2v", {"aspect_ratio": "9:16"}, {"start_image": 1})
    assert endpoint == "wan-2-2-i2v-720" and p["size"] == "720*1280" and p["image"] == IMG
    assert price("pruna-p-video-edit", "pruna-p-video-edit@runpod", "edit", {"draft": False}, [clip()]) == pytest.approx(0.45)


def test_endpoint_templates_need_their_params():
    from studio.registry.mapping import endpoint_for

    assert endpoint_for("a/{tier}/b", {"tier": "pro"}) == "a/pro/b"
    with pytest.raises(ValueError, match="tier"):
        endpoint_for("a/{tier}/b", {})
