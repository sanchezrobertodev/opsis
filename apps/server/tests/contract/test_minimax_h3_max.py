"""H3 Max is a separate model with its own schema, limits, and regular-rate budget."""

from decimal import Decimal

import pytest

from studio.config import get_settings
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.validate import MediaRef, RequestError, normalize

REG = load_registry(get_settings().resolve_registry_dir())
SPEC = REG.model("minimax-h3-max")
ROUTE = SPEC.route("minimax-h3-max@fal")


def test_minimax_h3_max_is_separate_from_h3():
    h3 = REG.model("minimax-h3")
    assert SPEC.id != h3.id and SPEC.name != h3.name
    assert SPEC.family == h3.family == "minimax"
    assert SPEC.status == "active" and SPEC.prompt.dialogue
    assert ROUTE.provider == "fal"


@pytest.mark.parametrize(("resolution", "wire", "cost"), [
    ("480p", "480P", "0.25"), ("768p", "768P", "0.40"), ("1080p", "1080P", "0.80"),
])
def test_minimax_h3_max_resolution_mapping_and_regular_price(resolution, wire, cost):
    req = normalize(SPEC, "t2v", "A sailboat on a lake", {"resolution": resolution}, [])
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {})
    assert endpoint == "minimax/h3-max/text-to-video"
    assert body["resolution"] == wire and body["duration"] == 5
    assert body["prompt_expansion_mode"] == "disabled"  # Required by every Max endpoint.
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal(cost)


def test_minimax_h3_max_accepts_one_second_and_maps_first_last_frames():
    refs = [MediaRef(role=role, asset_id=role, kind="image") for role in ("start_image", "end_image")]
    req = normalize(SPEC, "flf2v", "The sun sets", {"duration": 1}, refs)
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {
        "start_image": "https://example.test/first.png", "end_image": "https://example.test/last.png",
    })
    assert endpoint == "minimax/h3-max/image-to-video"
    assert body["image_url"] == "https://example.test/first.png"
    assert body["end_image_url"] == "https://example.test/last.png"
    assert "aspect_ratio" not in body
    assert body["duration"] == 1 and body["prompt_expansion_mode"] == "disabled"
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal("0.05")


def test_minimax_h3_max_reference_stays_within_included_token_allowance():
    refs = [MediaRef(role="image_references", asset_id="ref", kind="image", width=2560, height=1024)]
    req = normalize(SPEC, "reference", "Image 1 shows the character", {}, refs)
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {"image_references": ["https://example.test/ref.png"]})
    assert endpoint == "minimax/h3-max/reference-to-video"
    assert body["reference_image_urls"] == ["https://example.test/ref.png"]
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal("0.25")
    with pytest.raises(RequestError):
        normalize(SPEC, "reference", "Image 1 shows the character", {}, [*refs,
            MediaRef(role="image_references", asset_id="second", kind="image"),
        ])


@pytest.mark.parametrize(("width", "height"), [(2048, 2048), (2560, 1024), (1024, 2560)])
def test_minimax_h3_max_reference_dimension_boundaries_are_included(width, height):
    refs = [MediaRef(role="image_references", asset_id="ref", kind="image", width=width, height=height)]
    req = normalize(SPEC, "reference", "Image 1 shows the character", {}, refs)
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal("0.25")


@pytest.mark.parametrize(("width", "height", "message"), [
    (4096, 4096, "resize it first"), (2049, 2048, "resize it first"),
    (2561, 1024, "crop it first"), (1024, 2561, "crop it first"),
    (None, 1024, "known image dimensions"), (1024, None, "known image dimensions"),
    (0, 1024, "known image dimensions"), (-1, 1024, "known image dimensions"),
])
def test_minimax_h3_max_rejects_invalid_reference_dimensions(width, height, message):
    refs = [MediaRef(role="image_references", asset_id="ref", kind="image", width=width, height=height)]
    with pytest.raises(RequestError, match=message):
        normalize(SPEC, "reference", "Image 1 shows the character", {}, refs)


def test_minimax_h3_frames_are_not_subject_to_reference_pixel_limit():
    refs = [MediaRef(role="start_image", asset_id="frame", kind="image", width=4096, height=4096)]
    normalize(SPEC, "i2v", "The sun sets", {}, refs)


@pytest.mark.parametrize("params", [{"duration": 0}, {"duration": 16}, {"resolution": "4k"},
                                 {"prompt_expansion_mode": "fast"}])
def test_minimax_h3_max_rejects_unsupported_choices(params):
    with pytest.raises(RequestError):
        normalize(SPEC, "t2v", "A sailboat on a lake", params, [])
