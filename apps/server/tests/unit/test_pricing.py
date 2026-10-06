from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from studio.registry.pricing import dims_for, estimate
from studio.registry.schema import Multiplier, Pricing, Rate


def per_second(*rates: Rate, multipliers=()) -> Pricing:
    return Pricing(basis="per_output_second", rates=list(rates), multipliers=list(multipliers))


def test_dims_landscape_portrait_and_image_sizes():
    assert dims_for("720p", "16:9") == (1280, 720)
    assert dims_for("720p", "9:16") == (720, 1280)
    assert dims_for("1080p", "1:1") == (1080, 1080)
    assert dims_for("2k", "16:9") == (2048, 1152)  # image sizes: "2k" = 2048 px long side
    assert dims_for("1440p", "16:9") == (2560, 1440)
    assert dims_for("1k", "16:9") == (1024, 576)


def test_per_second_uses_duration():
    p = per_second(Rate(usd=0.4))
    assert estimate(p, {"duration": 8}).usd == Decimal("3.2")


def test_most_specific_rate_wins_and_fallback_applies():
    p = per_second(
        Rate(usd=0.084),
        Rate(when={"generate_audio": True}, usd=0.112),
        Rate(when={"generate_audio": True, "resolution": "1080p"}, usd=0.14),
    )
    assert estimate(p, {"duration": 5, "generate_audio": False}).usd == Decimal("0.42")
    assert estimate(p, {"duration": 5, "generate_audio": True, "resolution": "720p"}).usd == Decimal("0.56")
    assert estimate(p, {"duration": 5, "generate_audio": True, "resolution": "1080p"}).usd == Decimal("0.7")


def test_enum_values_match_by_string():
    p = per_second(Rate(when={"duration": 8}, usd=1.0), Rate(usd=0.5))
    assert estimate(p, {"duration": "8"}).rate_usd == 1.0


def test_multiplier_and_minimum():
    p = Pricing(basis="per_request", rates=[Rate(usd=0.01)], multipliers=[Multiplier(when={"hd": True}, factor=3)],
                minimum_usd=0.05)
    assert estimate(p, {"hd": True}).usd == Decimal("0.05")  # 0.03 raised to the minimum


def test_per_image_counts_outputs():
    p = Pricing(basis="per_image", rates=[Rate(when={"resolution": "2k"}, usd=0.101)])
    assert estimate(p, {"resolution": "2k", "num_images": 3}).usd == Decimal("0.303")


def test_video_token_quantity_expression():
    p = Pricing(basis="per_video_token", quantity="width*height*fps*duration/1024",
                rates=[Rate(usd=0.0214, per=1000)])
    est = estimate(p, {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9", "fps": 24})
    assert est.quantity == 1280 * 720 * 24 * 5 / 1024
    assert est.usd > 0


def test_formula_basis():
    p = Pricing(basis="formula", formula="0.1 + duration * 0.02")
    assert estimate(p, {"duration": 10}).usd == Decimal("0.3")


@given(st.floats(min_value=0, max_value=60), st.floats(min_value=0, max_value=60),
       st.floats(min_value=0, max_value=2))
def test_never_negative_and_monotonic_in_duration(d1, d2, rate):
    p = per_second(Rate(usd=rate))
    lo, hi = sorted([d1, d2])
    a, b = estimate(p, {"duration": lo}).usd, estimate(p, {"duration": hi}).usd
    assert a >= 0 and b >= 0
    assert a <= b
