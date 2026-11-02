"""Cutting a song into shots at the pauses in its lyrics."""

from __future__ import annotations

import itertools

from studio.media.transcript import mock, shots


def test_shots_cover_the_song_and_cut_at_pauses():
    words = mock(30.0)["words"]  # a "la" every half second, a breath after every eight
    cut = shots(words, 30.0, target_s=6, min_s=3, max_s=10, max_shots=24)
    assert cut[0]["start"] == 0 and cut[-1]["end"] == 30.0
    assert all(a["end"] == b["start"] for a, b in itertools.pairwise(cut))
    assert all(3 <= s["end"] - s["start"] <= 10 for s in cut)
    breaths = {round((a["end"] + b["start"]) / 2, 3) for a, b in itertools.pairwise(words)
               if b["start"] - a["end"] > 0.5}
    assert all(s["end"] in breaths for s in cut[:-1])  # every cut falls in a breath
    # each shot's words are timed from its own start, and every word lands in exactly one shot
    assert sum(len(s["words"]) for s in cut) == len(words)
    assert all(0 <= w["start"] < w["end"] <= s["end"] - s["start"] for s in cut for w in s["words"])


def test_an_instrumental_is_cut_evenly_and_a_short_song_is_one_shot():
    cut = shots([], 20.0, target_s=6, min_s=3, max_s=10, max_shots=24)
    assert [s["end"] for s in cut] == [6.0, 12.0, 20.0] and all(s["text"] == "" for s in cut)
    assert len(shots(mock(8.0)["words"], 8.0, target_s=6, min_s=3, max_s=10, max_shots=24)) == 1


def test_never_more_shots_than_allowed_even_with_a_pause_every_few_seconds():
    words = mock(180.0)["words"]  # a breath every five seconds: 36 shots if cut at every one
    cut = shots(words, 180.0, target_s=7.5, min_s=3, max_s=10, max_shots=24)
    assert len(cut) <= 24 and cut[-1]["end"] == 180.0
    assert all(s["end"] - s["start"] <= 10 + 1e-6 for s in cut)
    assert len(shots([], 240.0, target_s=10, min_s=3, max_s=10, max_shots=24)) == 24


def test_a_target_past_the_models_longest_clip_never_makes_a_long_or_empty_shot():
    # a 6 s target on a 5 s video model once cut a 12 s song into 6 + 6 + 0 and 35.5 s into a negative last shot
    for total, max_shots in ((12.0, 3), (12.0, 24), (35.5, 8), (35.5, 24)):
        for words in ([], mock(total)["words"]):
            cut = shots(words, total, target_s=6, min_s=3, max_s=5, max_shots=max_shots)
            lengths = [s["end"] - s["start"] for s in cut]
            assert cut[0]["start"] == 0 and cut[-1]["end"] == total and len(cut) <= max_shots, lengths
            assert all(0 < n <= 5 + 1e-6 for n in lengths), (total, bool(words), lengths)


def test_rounding_a_cut_never_costs_one_more_shot_or_an_empty_one():
    # a cut rounded to the millisecond once left the rest a hair over max_s: 23 shots of 22, or a 0 s shot and a crash
    at = [round(k * 0.3001234, 6) for k in range(429)]
    words = [{"text": "w", "start": t, "end": round(t + 0.2, 6)} for t in at]
    for words_, total, max_s, max_shots in ((words, 129.074, 7, 22), ([], 8.717, 3, 4), ([], 8.403, 3, 31)):
        cut = shots(words_, total, target_s=max_s, min_s=min(3, max_s), max_s=max_s, max_shots=max_shots)
        lengths = [s["end"] - s["start"] for s in cut]
        assert len(cut) <= max_shots and all(0 < n <= max_s + 1e-6 for n in lengths), (total, lengths)
