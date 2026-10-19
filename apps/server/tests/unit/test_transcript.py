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
