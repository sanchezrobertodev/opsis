"""Character LoRAs: the trainer's request, and how a mentioned character's weights reach Wan 2.2 on fal."""

from __future__ import annotations

from studio.config import get_settings
from studio.pipelines.recipes.lora_train import default_trigger
from studio.providers.common import best_mime
from studio.registry.loader import load_registry
from studio.registry.mapping import build_payload
from studio.registry.validate import MediaRef, normalize

REG = load_registry(get_settings().resolve_registry_dir())


def _payload(model_id: str, mode: str, prompt: str, params: dict, media: list[MediaRef], staged: dict) -> dict:
    spec = REG.models[model_id]
    route = spec.routes[0]
    _, payload, _ = build_payload(spec, route, normalize(spec, mode, prompt, params, media), staged)
    return payload


def test_a_characters_two_experts_go_to_their_own_transformers():
    payload = _payload(
        "wan-2.2-image", "t2i", "Maya (mayazq01) on a pier", {"lora_scale": 0.8},
        [MediaRef("lora", "low1", "lora"), MediaRef("lora_high", "high1", "lora")],
        {"lora": ["https://fal.media/low1.safetensors"], "lora_high": ["https://fal.media/high1.safetensors"]})
    assert payload["loras"] == [
        {"path": "https://fal.media/high1.safetensors", "scale": 0.8, "transformer": "high"},
        {"path": "https://fal.media/low1.safetensors", "scale": 0.8, "transformer": "low"},
    ]
    assert not [k for k in payload if k.startswith("_")] and "lora_scale" not in payload
    assert payload["image_size"] == "landscape_16_9"


def test_without_a_mentioned_character_no_loras_are_sent():
    payload = _payload("wan-2.2-lora", "t2v", "A pier in a storm", {"duration": 5}, [], {})
    assert "loras" not in payload and payload["num_frames"] == 81 and payload["resolution"] == "720p"


def test_a_single_file_lora_loads_on_both_experts():
    payload = _payload("wan-2.2-lora", "t2v", "x", {}, [MediaRef("lora", "a", "lora")],
                       {"lora": ["https://fal.media/a.safetensors"]})
    assert payload["loras"] == [{"path": "https://fal.media/a.safetensors", "scale": 1.0, "transformer": "both"}]


def test_the_trainer_learns_from_the_zip_with_captions_written_for_it():
    payload = _payload("wan-2.2-lora-trainer", "character", "", {"trigger_phrase": "mayazq01", "steps": 600},
                       [MediaRef("training_data", "z", "archive")], {"training_data": ["https://fal.media/p.zip"]})
    assert payload == {"trigger_phrase": "mayazq01", "steps": 600, "use_face_cropping": False,
                       "training_data_url": "https://fal.media/p.zip", "include_synthetic_captions": True,
                       "is_style": False}


def test_trigger_words_are_made_up_words():
    word = default_trigger("maya-lighthouse", "01a11d4d-6ed8-7020-b3bf-bec0e6211c6d")
    assert word == "mayalighthouzq1c6d" and word.isalnum() and word.islower()
    assert default_trigger("Ö", "abcd").startswith("chrzq")


def test_downloaded_lora_files_and_zips_get_a_type_without_a_media_mime():
    assert best_mime(None, "binary/octet-stream", kind="lora") == "application/octet-stream"
    assert best_mime(None, kind="archive") == "application/zip"
