"""Character LoRAs: the trainer's request, and how a mentioned character's weights reach Wan 2.2 on fal."""

from __future__ import annotations

from dataclasses import replace

import pytest

from studio.api.routers.elements import ElementPatch
from studio.config import get_settings
from studio.director.elements import identities
from studio.pipelines.recipes.lora_train import LoraTrainInput, LoraTrainRecipe, default_trigger
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


def test_flux_klein_takes_each_lora_as_path_and_scale_with_its_prompt_left_as_written():
    payload = _payload("flux-3-klein", "t2i", "Maya (mayazq01) on a pier", {"lora_scale": 0.7, "aspect_ratio": "3:4"},
                       [MediaRef("lora", "a", "lora"), MediaRef("lora", "b", "lora")],
                       {"lora": ["https://fal.media/a.safetensors", "https://fal.media/b.safetensors"]})
    assert payload["loras"] == [{"path": "https://fal.media/a.safetensors", "scale": 0.7},
                                {"path": "https://fal.media/b.safetensors", "scale": 0.7}]
    # expansion could reword the made-up trigger word the identity answers to
    assert payload["enable_prompt_expansion"] is False and payload["image_size"] == "portrait_4_3"
    assert not [k for k in payload if k.startswith("_")] and "lora_scale" not in payload


def test_the_flux_trainer_sends_its_own_params_and_says_its_lora_is_for_flux_klein():
    payload = _payload("flux-3-klein-lora-trainer", "character", "", {"trigger_phrase": "mayazq01", "steps": 500},
                       [MediaRef("training_data", "z", "archive")], {"training_data": ["https://fal.media/p.zip"]})
    assert payload == {"trigger_phrase": "mayazq01", "steps": 500, "resolution": 1024,
                       "images_data_url": "https://fal.media/p.zip"}
    assert REG.models["flux-3-klein-lora-trainer"].lora_base() == "flux-3-klein"
    assert REG.models["wan-2.2-lora-trainer"].lora_base() == REG.models["wan-2.2-image"].lora_base() == "wan-2.2-a14b"
    assert REG.models["flux-3-klein"].lora_base() == "flux-3-klein" and REG.models["veo-3.1"].lora_base() is None


def test_a_page_loaded_before_identities_were_per_base_can_still_save():
    """Its editor sent clear_lora: false with every save; now the field names the base to forget."""
    assert ElementPatch.model_validate({"clear_voice": False, "clear_lora": False}).clear_lora is None
    assert ElementPatch.model_validate({"clear_lora": "flux-3-klein"}).clear_lora == "flux-3-klein"


def test_identities_are_kept_per_base_and_one_kept_before_reads_as_its_bases():
    wan = {"base": "wan-2.2-a14b", "trigger": "mayazq01", "low": "l", "high": "h"}
    flux = {"base": "flux-3-klein", "trigger": "mayazq01", "low": "f", "high": None}
    assert identities({"wan-2.2-a14b": wan, "flux-3-klein": flux}) == {"wan-2.2-a14b": wan, "flux-3-klein": flux}
    assert identities(wan) == {"wan-2.2-a14b": wan}  # stored flat by a worker from before the migration
    assert identities({**wan, "base": None}) == {} and identities(None) == {} and identities({"x": "junk"}) == {}


def test_a_run_stored_before_there_was_a_trainer_to_pick_plans_the_same_steps():
    """No `trainer_model_id`: Wan's trainer and portrait with the very same inputs, so nothing finished is redone; FLUX
    3 Klein's trainer leaves out the face crop it doesn't have and draws the portrait on FLUX 3 Klein."""
    stored = {"element_id": "01a11d4d-6ed8-7020-b3bf-bec0e6211c6d", "slug": "maya", "photos": [f"p{i}" for i in range(10)],
              "trigger": "mayazq0001", "steps": 600}
    settled = {"dataset": {"asset_id": "zip1"}, "train": {"asset_id": "low1"}, "attach": {"base": "wan-2.2-a14b"}}
    plan = {s.key: s.input for s in LoraTrainRecipe().plan(LoraTrainInput(**stored), settled, REG)}
    assert plan["train"] == {"model_id": "wan-2.2-lora-trainer", "mode": "character", "prompt": "", "direction": None,
                             "params": {"trigger_phrase": "mayazq0001", "steps": 600, "face_crop": False},
                             "media": [{"role": "training_data", "asset_id": "zip1"}]}
    assert plan["preview"] == {"model_id": "wan-2.2-image", "mode": "t2i", "params": {"aspect_ratio": "3:4"},
                               "media": [], "direction": None,
                               "prompt": "A natural portrait photo of @maya, soft daylight, looking at the camera."}
    flux = LoraTrainInput(**stored, trainer_model_id="flux-3-klein-lora-trainer")
    plan = {s.key: s.input for s in LoraTrainRecipe().plan(flux, {**settled, "attach": {"base": "flux-3-klein"}}, REG)}
    assert plan["train"]["params"] == {"trigger_phrase": "mayazq0001", "steps": 600}
    assert plan["preview"]["model_id"] == "flux-3-klein"


def test_a_run_whose_trainer_or_portrait_model_left_the_registry_ends_instead_of_waiting():
    """A deploy drops a model mid-run: a run still to train fails (the engine says it couldn't plan it), one that has
    trained attaches the identity and finishes without the portrait no model here can draw."""
    inp = LoraTrainInput(element_id="e1", slug="maya", photos=[f"p{i}" for i in range(10)])
    dataset = {"dataset": {"asset_id": "zip1"}}
    without = replace(REG, models={k: v for k, v in REG.models.items() if k not in ("wan-2.2-lora-trainer",
                                                                                     "wan-2.2-image")})
    with pytest.raises(ValueError, match="isn't a LoRA trainer in the registry"):
        LoraTrainRecipe().plan(inp, dataset, without)
    trained = {**dataset, "train": {"asset_id": "low1"}, "attach": {"base": "wan-2.2-a14b"}}
    assert [s.key for s in LoraTrainRecipe().plan(inp, trained, without)] == ["dataset", "attach"]
    assert LoraTrainRecipe().result(inp, trained) == {"element_id": "e1", "trigger": inp.trigger,
                                                      "lora": {"base": "wan-2.2-a14b"}, "asset_id": None}
