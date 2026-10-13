"""H3's fal field mapping and costs, checked without making provider calls."""

from decimal import Decimal

import pytest

from studio.config import get_settings
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.pricing import estimate
from studio.registry.validate import MediaRef, RequestError, normalize

REG = load_registry(get_settings().resolve_registry_dir())
SPEC = REG.model("minimax-h3")
ROUTE = SPEC.route("minimax-h3@fal")


@pytest.mark.parametrize(("resolution", "wire", "cost"), [
    ("480p", "480P", "0.25"), ("768p", "768P", "0.30"),
    ("2k", "2K", "0.65"), ("4k", "4K", "0.80"),
])
def test_minimax_h3_resolution_mapping_and_price(resolution, wire, cost):
    req = normalize(SPEC, "t2v", "A sailboat on a lake", {"resolution": resolution}, [])
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {})
    assert endpoint == "minimax/h3/text-to-video"
    assert body["resolution"] == wire
    assert body["duration"] == 5
    assert body["prompt_expansion_mode"] == "disabled"
    assert "generate_audio" not in body  # H3 includes sound, with no audio toggle on this endpoint.
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal(cost)


def test_minimax_h3_first_last_frames_follow_input_aspect():
    refs = [MediaRef(role=role, asset_id=role, kind="image") for role in ("start_image", "end_image")]
    req = normalize(SPEC, "flf2v", "The sun sets", {}, refs)
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {
        "start_image": "https://example.test/first.png", "end_image": "https://example.test/last.png",
    })
    assert endpoint == "minimax/h3/image-to-video"
    assert body["image_url"] == "https://example.test/first.png"
    assert body["end_image_url"] == "https://example.test/last.png"
    assert "aspect_ratio" not in body


def test_minimax_h3_references_stay_in_free_image_allowance():
    refs = [MediaRef(role="image_references", asset_id=str(i), kind="image") for i in range(5)]
    req = normalize(SPEC, "reference", "Image 1 shows the character", {}, refs)
    urls = [f"https://example.test/{i}.png" for i in range(5)]
    endpoint, body, _ = build_payload(SPEC, ROUTE, req, {"image_references": urls})
    assert endpoint == "minimax/h3/reference-to-video"
    assert body["reference_image_urls"] == urls
    assert estimate(ROUTE.pricing, req.params, req.prompt, req.media, req.mode).usd == Decimal("0.25")
    with pytest.raises(RequestError):
        normalize(SPEC, "reference", "Image 1 shows the character", {}, [*refs,
            MediaRef(role="image_references", asset_id="sixth", kind="image"),
        ])


@pytest.mark.parametrize("duration", [4, 16])
def test_minimax_h3_rejects_unsupported_durations(duration):
    with pytest.raises(RequestError):
        normalize(SPEC, "t2v", "A sailboat on a lake", {"duration": duration}, [])
