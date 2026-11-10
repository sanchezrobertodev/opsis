"""Which models can speak a shot's lines in the speakers' own voices (`takes_voices`)."""

from __future__ import annotations

from studio.config import get_settings
from studio.pipelines.recipes.cinema import SceneShot, TakeShot, takes_voices, voice_mode
from studio.registry.loader import load_registry

REG = load_registry(get_settings().resolve_registry_dir())


def shot(*samples: tuple[str | None, float]) -> SceneShot:
    lines = [{"who": f"@c{i}", "text": "Hello there.", "voice_id": "v", "voice_sample_id": sid, "voice_sample_seconds": s}
             for i, (sid, s) in enumerate(samples)]
    return SceneShot(key="sone", prompt="x", duration=5, direction={"dialogue": lines})


def test_h3_takes_voices_within_its_limits():
    h3 = REG.models["minimax-h3"]
    mode = voice_mode(h3, set())
    assert mode is not None and mode.id == "reference"
    assert takes_voices(h3, mode, shot(("a", 6), ("b", 8)))
    assert not takes_voices(h3, mode, shot(("a", 10), ("b", 10)))  # 15 s of reference audio in all
    assert not takes_voices(h3, mode, shot(("a", 20)))  # 15 s at most each
    assert not takes_voices(h3, mode, shot(("a", 1)))  # 2 s at least
    assert not takes_voices(h3, mode, shot(("a", 5), (None, 0)))  # a speaker with no recording


def test_models_without_reference_audio_never_take_voices():
    kling = REG.models["kling-3.0"]
    assert voice_mode(kling, set()) is None
    assert not any(takes_voices(kling, m, shot(("a", 5))) for m in kling.modes)


def test_a_local_voice_model_speaks_from_the_speakers_recording():
    import uuid

    from studio.pipelines.recipes.cinema import CinemaTakesInput, CinemaTakesRecipe

    sample = str(uuid.uuid4())
    lines = [{"who": "@nova", "text": "It goes dark tonight.", "voice_sample_id": sample, "voice_sample_seconds": 6},
             {"who": "@kai", "text": "Then we light it.", "voice_id": "Aiden"},
             {"who": "@mira", "text": "Who's there?"}]
    take = TakeShot(key="sone", prompt="x", duration=5, direction={"dialogue": lines})
    recipe = CinemaTakesRecipe()
    for voice_model, sample_only_ok in (("qwen3-tts", True), ("eleven-v4", False)):
        inp = CinemaTakesInput(project_id=uuid.uuid4(), model_id="kling-3.0", takes=1, shots=[take],
                               voice_model=voice_model)
        assert (recipe.validate(inp, REG) == []) is sample_only_ok, recipe.validate(inp, REG)
    steps = {s.key: s.input for s in recipe.plan(inp.model_copy(update={"voice_model": "qwen3-tts"}), {}, REG)}
    assert steps["line[sone:1]"]["mode"] == "clone"
    assert steps["line[sone:1]"]["media"] == [{"role": "audio_references", "asset_id": sample}]
    assert (steps["line[sone:2]"]["mode"], steps["line[sone:2]"]["params"]) == ("tts", {"voice_id": "Aiden"})
    assert (steps["line[sone:3]"]["mode"], steps["line[sone:3]"]["params"]) == ("tts", {})
    assert "media" not in steps["line[sone:3]"]  # a plain line's step input is as it was before cloning came in
    jobs = [j for j in recipe.jobs(inp.model_copy(update={"voice_model": "qwen3-tts"}), REG) if j.model_id == "qwen3-tts"]
    assert [j.mode for j in jobs] == ["clone", "tts", "tts"] and jobs[0].media == (("audio_references", sample),)


def test_a_picture_lip_sync_model_remakes_the_take_from_its_first_frame():
    import uuid

    from studio.pipelines.recipes.cinema import CinemaTakesInput, CinemaTakesRecipe

    lines = [{"who": "@nova", "text": "It goes dark tonight.", "voice_id": "v"}]
    take = TakeShot(key="sone", prompt="Nova at the window", duration=5, direction={"dialogue": lines})
    inp = CinemaTakesInput(project_id=uuid.uuid4(), model_id="kling-3.0", takes=1, shots=[take],
                           lipsync_model_id="infinitetalk")
    recipe = CinemaTakesRecipe()
    assert recipe.validate(inp, REG) == []

    def made() -> dict:
        return {"asset_id": str(uuid.uuid4())}

    settled = {"take[sone:1]": made(), "line[sone:1]": made()}
    steps = {s.key: s for s in recipe.plan(inp, settled, REG)}
    assert steps["track[sone]"].input["pad_to"] == 5.0  # the remake is as long as the shot
    settled["track[sone]"] = made()
    steps = {s.key: s for s in recipe.plan(inp, settled, REG)}
    assert steps["face[sone:1]"].input == {"op": "first_frame", "asset_id": settled["take[sone:1]"]["asset_id"]}
    assert "lip[sone:1]" not in steps
    settled["face[sone:1]"] = made()
    lip = {s.key: s for s in recipe.plan(inp, settled, REG)}["lip[sone:1]"]
    assert lip.input["media"] == [{"role": "character_image", "asset_id": settled["face[sone:1]"]["asset_id"]},
                                  {"role": "input_audio", "asset_id": settled["track[sone]"]["asset_id"]}]
    assert lip.input["prompt"] == "Nova at the window" and lip.depends_on == ("face[sone:1]", "track[sone]")
    jobs = [j for j in recipe.jobs(inp, REG) if j.model_id == "infinitetalk"]
    assert [j.roles for j in jobs] == [("character_image", "input_audio")]
    assert jobs[0].planned_seconds == (("input_audio", 5.0),)

    # a clip-driven model moves the take's mouths, with the track as it was
    clip = inp.model_copy(update={"lipsync_model_id": "infinitetalk-dub"})
    steps = {s.key: s for s in recipe.plan(clip, settled, REG)}
    assert "pad_to" not in steps["track[sone]"].input and "face[sone:1]" not in steps
    assert steps["lip[sone:1]"].input["media"][0] == {"role": "input_video", "asset_id": settled["take[sone:1]"]["asset_id"]}
