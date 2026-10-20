"""Faceless Studio pieces that need no database: catalog, writer helpers, recipe choices, timeline and captions."""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.faceless.catalog import CaptionLook, get_catalog
from studio.faceless.writer import _mock_plan, _mock_script, sentences, split_narration, tidy_narration
from studio.media.assemble import TAIL_S, frame_size, ken_burns, timeline
from studio.media.captions import ass_color, cues, fit_words, place_words, to_ass, to_srt
from studio.media.speech import estimate_words
from studio.pipelines.recipes.common import fit_duration, mode_with_op
from studio.pipelines.recipes.faceless import (
    FacelessInput,
    FacelessRecipe,
    clip_key,
    still_key,
    visual_prompt,
)
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())


def test_catalog_themes_styles_and_real_voices():
    cat = get_catalog()
    assert [t.id for t in cat.themes] == ["education", "history", "kids", "storytelling", "creature-documentary"]
    assert len(cat.styles) == 13 and cat.style("wildlife-documentary") is not None
    assert cat.theme("creature-documentary").voice_id == "BNgbHR0DNeZixGQVzloa"  # David, deep British narrator
    voices = set(REG.model("eleven-v4").param("voice_id").options)
    assert all(t.voice_id in voices for t in cat.themes)


def test_writer_helpers():
    assert tidy_narration("# Title\n- **Once** upon a time [music swells]\n\n\n\nThe end.") == \
        "Title\nOnce upon a time\n\nThe end."
    assert sentences("One. Two!  Three?") == ["One.", "Two!", "Three?"]
    text = " ".join(f"Sentence number {i} is here." for i in range(20))
    blocks = split_narration(text, 6.0)
    assert " ".join(blocks) == text and all(len(b) < 6 * 14.5 * 1.5 for b in blocks)
    script = _mock_script("A topic", 150)
    assert len(script.narration.split()) >= 150
    plan = _mock_plan(script.narration, "motion")
    assert " ".join(b.narration for b in plan.blocks) == " ".join(script.narration.split())
    assert all(b.motion for b in plan.blocks)


def test_fit_duration_picks_the_shortest_covering_value():
    veo = REG.model("veo-3.1")
    assert fit_duration(veo, "i2v", 5.1) == 6 and fit_duration(veo, "i2v", 30) == 8
    music = REG.model("eleven-music")
    assert fit_duration(music, "music", 42.3) == 45 and fit_duration(music, "music", 3) == 10
    assert fit_duration(REG.model("nano-banana-2"), "t2i", 5) is None
    assert mode_with_op(REG.model("eleven-v4"), "tts").id == "tts"


def test_failed_clip_falls_back_to_its_still():
    r = FacelessRecipe()
    inp = FacelessInput(topic="x", format="motion")
    still = {"asset_id": "s1", "asset_ids": ["s1"]}
    assert r._visual(inp, {still_key(1): still}, 1) is None  # the clip hasn't settled yet
    assert r._visual(inp, {still_key(1): still, clip_key(1): {"failed": "moderation"}}, 1) == "s1"
    assert r._visual(inp, {still_key(1): still, clip_key(1): {"asset_id": "c1"}}, 1) == "c1"
    assert r._visual(FacelessInput(topic="x"), {still_key(1): still}, 1) == "s1"


def test_visual_prompt_carries_recurring_looks_and_the_style():
    roster = [{"name": "Ada", "look": "a girl in a yellow raincoat"}, {"name": "the mill", "look": "a stone mill"}]
    p = visual_prompt("Wide shot of Ada running.", roster, "A wet northern town", "watercolor illustration")
    assert p == ("Wide shot of Ada running. Ada: a girl in a yellow raincoat. A wet northern town. "
                 "watercolor illustration. no text, letters or logos in the image.")


def test_validation_names_what_is_wrong():
    r = FacelessRecipe()
    errors = r.validate(FacelessInput(topic="", theme="sci-fi", image_model="eleven-v4", format="motion",
                                      video_model="nano-banana-2"), REG)
    assert "unknown theme 'sci-fi'" in errors and "give a topic or paste a script" in errors
    assert "Eleven v4 can't make images from text" in errors and "Nano Banana 2 can't animate a still" in errors


def test_timeline_frame_sizes_and_ken_burns():
    tl = timeline([2.0, 3.0])
    assert tl.starts == [0.0, 2.35] and tl.total == pytest.approx(2.35 + 3.0 + TAIL_S)
    assert frame_size("9:16", "720p") == (720, 1280) and frame_size("16:9") == (1920, 1080)
    assert "zoompan=z='1+0.12*on/47'" in ken_burns("in", 48, 1280, 720, 24)


def test_captions_srt_and_ass():
    words = place_words([estimate_words("Hello there, old friend."), fit_words("Goodbye now.", 2.0)], [0.0, 3.0])
    assert words[-1]["end"] == pytest.approx(5.0, abs=0.01)
    items = cues(words, max_chars=20)
    assert [c.text for c in items] == ["Hello there, old\nfriend.", "Goodbye now."]
    srt = to_srt(items)
    assert srt.startswith("1\n00:00:00,000 --> ") and "\n2\n00:00:03,000 --> " in srt
    look = CaptionLook(highlight="#FF0000", uppercase=True)
    ass = to_ass(items, look, 1280, 720)
    assert "PlayResX: 1280" in ass and ass_color("#FF0000") == "&H000000FF"
    assert ass.count("Dialogue:") == len(words)  # one event per spoken word, that word highlighted
    assert r"{\1c&H000000FF}HELLO{\r} THERE," in ass


def test_sam_points_become_pixels_and_need_a_size():
    from studio.providers.transforms.segment import sam_points
    from studio.registry.validate import MediaRef, normalize

    spec = REG.model("sam-3-image")
    image = MediaRef("input_image", "i", "image", width=1001, height=501)
    req = normalize(spec, "segment", "", {"points": [[0.5, 0.5, 1], [1.0, 0.0, 0]]}, [image])
    out = sam_points(spec=spec, route=spec.routes[0], req=req, payload={"point_prompts": req.params["points"]})
    assert out == {"point_prompts": [{"x": 500, "y": 250, "label": 1}, {"x": 1000, "y": 0, "label": 0}], "prompt": ""}
    req.media[0].width = None
    with pytest.raises(ValueError, match="size is unknown"):
        sam_points(spec=spec, route=spec.routes[0], req=req, payload={"point_prompts": [[0.5, 0.5, 1]]})


def test_planned_narration_is_cut_from_the_approved_script():
    from studio.faceless.writer import narration_from_script

    script = "The keeper climbed the stairs. She lit the lamp—one last time! Then she went home."
    # case and punctuation drift is fine: the script's own text is used
    assert narration_from_script(script, ["the keeper climbed the stairs", "She lit the lamp, one last time.",
                                          "then she went home"]) == [
        "The keeper climbed the stairs.", "She lit the lamp—one last time!", "Then she went home."]
    # reworded, reordered, duplicated or dropped words are not
    assert narration_from_script(script, ["The keeper walked up the stairs.", "She lit the lamp one last time!",
                                          "Then she went home."]) is None
    assert narration_from_script(script, ["She lit the lamp one last time!", "The keeper climbed the stairs.",
                                          "Then she went home."]) is None
    assert narration_from_script(script, [script, "Then she went home."]) is None
    assert narration_from_script(script, ["The keeper climbed the stairs."]) is None
    assert narration_from_script(script, [script, "..."]) is None


def test_tracking_starts_from_the_clicks_on_the_clip_frame():
    # fal's SAM 3 tracker refuses a mask on its own: it needs a prompt too, so the clicks that made it go along
    from studio.providers.transforms.segment import sam_points
    from studio.registry.mapping import build_payload
    from studio.registry.validate import MediaRef, normalize

    spec = REG.model("sam-3-video")
    clip = MediaRef("input_video", "v", "video", width=855, height=481)
    mask = MediaRef("mask", "m", "mask", width=855, height=481)
    req = normalize(spec, "track", "", {"points": [[0.5, 0.5, 1]], "frame_index": 12}, [clip, mask])
    route = spec.routes[0]
    _, payload, _ = build_payload(spec, route, req, {"input_video": "https://x.test/v.mp4", "mask": "https://x.test/m.png"})
    assert payload["point_prompts"] == [{"x": 427, "y": 240, "label": 1, "frame_index": 12}]
    assert payload["frame_index"] == 12
    assert payload["mask_url"] == "https://x.test/m.png" and payload["apply_mask"] is False
    out = sam_points(spec=spec, route=route, req=req, payload={"point_prompts": [[0.0, 1.0, 0]]})
    assert out["point_prompts"] == [{"x": 0, "y": 480, "label": 0, "frame_index": 0}]
