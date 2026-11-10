"""Which models can speak a shot's lines in the speakers' own voices (`takes_voices`)."""

from __future__ import annotations

from studio.config import get_settings
from studio.pipelines.recipes.cinema import SceneShot, takes_voices, voice_mode
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
