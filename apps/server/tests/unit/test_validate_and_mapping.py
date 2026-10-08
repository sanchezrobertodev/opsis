import pytest

from studio.registry.mapping import build_payload
from studio.registry.schema import ModelSpec
from studio.registry.validate import MediaRef, RequestError, normalize

SPEC = ModelSpec.model_validate({
    "id": "toy", "name": "Toy", "family": "toy", "vendor": "test", "output_type": "video",
    "prompt": {"max_chars": 50, "supports_negative": False},
    "aspect_ratios": ["16:9", "9:16"],
    "parameters": [
        {"name": "duration", "type": "enum", "options": [4, 8], "default": 4},
        {"name": "resolution", "type": "enum", "options": ["720p", "1080p"], "default": "720p"},
        {"name": "aspect_ratio", "type": "enum", "options": ["16:9", "9:16"], "default": "16:9"},
        {"name": "strength", "type": "float", "min": 0, "max": 1, "default": 0.5},
        {"name": "audio", "type": "bool", "default": False},
        {"name": "seed", "type": "seed"},
        {"name": "t2v_only", "type": "int", "min": 0, "max": 3, "default": 1, "modes": ["t2v"]},
    ],
    "rules": [{"when": {"resolution": "1080p"}, "require": {"duration": [8]}, "message": "1080p needs 8 s"}],
    "modes": [
        {"id": "t2v", "label": "T2V"},
        {"id": "i2v", "label": "I2V", "medias": [
            {"role": "start_image", "kinds": ["image"], "required": True},
            {"role": "image_references", "kinds": ["image"], "max": 2},
        ]},
    ],
    "routes": [{
        "id": "toy@x", "provider": "x",
        "modes": {"t2v": {"endpoint": "x/t2v"}, "i2v": {"endpoint": "x/i2v", "fixed": {"mode": "pro"}}},
        "map": {
            "params": {"duration": {"to": "duration", "transform": "suffix", "args": {"suffix": "s"}},
                       "strength": {"to": None}, "audio": "options.audio"},
            "media": {"start_image": "image_url", "image_references": "ref_urls"},
        },
        "pricing": {"basis": "per_request", "rates": [{"usd": 1}]},
    }],
})


def test_defaults_coercion_and_seed():
    req = normalize(SPEC, "t2v", " hello ", {"duration": "8", "audio": "true"}, [])
    assert req.prompt == "hello"
    assert req.params["duration"] == 8 and isinstance(req.params["duration"], int)
    assert req.params["audio"] is True
    assert req.params["resolution"] == "720p"
    assert isinstance(req.params["seed"], int)
    assert req.params["t2v_only"] == 1


def test_mode_scoped_params_and_unknown_params():
    req = normalize(SPEC, "i2v", "x", {"bogus": 1}, [MediaRef("start_image", "a1", "image")])
    assert "t2v_only" not in req.params
    assert any("bogus" in w for w in req.warnings)


def test_rules_ranges_and_prompt_errors():
    with pytest.raises(RequestError) as exc:
        normalize(SPEC, "t2v", "", {"resolution": "1080p", "strength": 2}, [])
    errs = exc.value.errors
    assert "prompt is required" in errs
    assert "1080p needs 8 s" in errs
    assert any("strength" in e for e in errs)
    with pytest.raises(RequestError):
        normalize(SPEC, "t2v", "x" * 51, {}, [])


def test_media_slots():
    with pytest.raises(RequestError) as exc:
        normalize(SPEC, "i2v", "x", {}, [MediaRef("image_references", "r1", "image")])
    assert any("required" in e for e in exc.value.errors)
    with pytest.raises(RequestError) as exc:
        normalize(SPEC, "i2v", "x", {}, [MediaRef("start_image", "a", "video")])
    assert any("must be image" in e for e in exc.value.errors)
    with pytest.raises(RequestError):
        normalize(SPEC, "i2v", "x", {}, [MediaRef("start_image", "a", "image")] +
                  [MediaRef("image_references", f"r{i}", "image") for i in range(3)])


def test_negative_prompt_dropped_when_unsupported():
    req = normalize(SPEC, "t2v", "x", {}, [], negative_prompt="blurry")
    assert req.negative_prompt is None
    assert any("negative" in w for w in req.warnings)


def test_build_payload_maps_transforms_drops_and_media():
    req = normalize(SPEC, "i2v", "go", {"duration": 8, "audio": True},
                    [MediaRef("start_image", "a", "image"), MediaRef("image_references", "r1", "image")])
    endpoint, payload, _ = build_payload(SPEC, SPEC.routes[0], req,
                                         {"start_image": ["https://x/a.png"], "image_references": "https://x/r1.png"})
    assert endpoint == "x/i2v"
    assert payload["prompt"] == "go"
    assert payload["duration"] == "8s"
    assert "strength" not in payload
    assert payload["options"] == {"audio": True}
    assert payload["image_url"] == "https://x/a.png"  # max=1 slot -> scalar
    assert payload["ref_urls"] == ["https://x/r1.png"]  # max>1 slot -> list
    assert payload["mode"] == "pro"  # fixed values from the route mode
    assert payload["resolution"] == "720p"  # unmapped params pass through


def test_media_longer_than_a_slot_allows_is_rejected():
    from studio.config import get_settings
    from studio.registry.loader import load_registry

    spec = load_registry(get_settings().resolve_registry_dir()).model("kling-o3-edit")  # clips up to 15 s
    ok = MediaRef("input_video", "v", "video", duration_ms=15_000)
    normalize(spec, "edit", "x", {}, [ok])
    long = MediaRef("input_video", "v", "video", duration_ms=16_000)
    with pytest.raises(RequestError, match=r"Clip to edit can be at most 15 s; this one is 16.0 s"):
        normalize(spec, "edit", "x", {}, [long])


def test_media_shorter_than_a_slot_allows_is_rejected():
    from studio.config import get_settings
    from studio.registry.loader import load_registry

    spec = load_registry(get_settings().resolve_registry_dir()).model("kling-o3-edit")  # fal refuses under 720p
    normalize(spec, "edit", "x", {}, [MediaRef("input_video", "v", "video", width=1280, height=720)])
    normalize(spec, "edit", "x", {}, [MediaRef("input_video", "v", "video")])  # size unknown: left to the provider
    small = MediaRef("input_video", "v", "video", width=854, height=480)
    with pytest.raises(RequestError, match=r"Clip to edit must be at least 720 px tall; this one is 480 px"):
        normalize(spec, "edit", "x", {}, [small])
    slow = MediaRef("input_video", "v", "video", width=1280, height=720, fps=16.0)  # a Wan clip
    with pytest.raises(RequestError, match=r"Clip to edit must be at least 23 fps; this one is 16 fps"):
        normalize(spec, "edit", "x", {}, [slow])
    normalize(spec, "edit", "x", {}, [MediaRef("input_video", "v", "video", width=1280, height=720, fps=23.976)])
