"""Cinema Studio pieces without a database: the shot plan's clean-up, the document, and the cut's filter graph."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.cinema.document import CinemaDocument, Settings, Shot, merged_direction
from studio.cinema.planner import PlannedScene, PlannedShot, ShotPlan, mock_plan, to_scenes
from studio.director.compiler import ShotDirection
from studio.director.library import get_library
from studio.media.timeline import join_graph


def test_plan_drops_unknown_ids_clamps_and_caps():
    plan = ShotPlan(scenes=[
        PlannedScene(heading="INT. ROOM", summary="s", shots=[
            PlannedShot(action="A", shot_size="enormous", angle="low", camera_moves=["dolly_in", "warp_drive"],
                        duration=40, continuity="continue"),
            PlannedShot(action="B", shot_size="close_up", duration=1, continuity="continue",
                        emotions=[{"who": "Maya", "emotion": "grief"}, {"who": "@maya", "emotion": "joy"},
                                  {"who": "@kai", "emotion": "smugness"}]),
        ]),
        PlannedScene(heading="EXT. ROAD", summary="t", shots=[PlannedShot(action="C"), PlannedShot(action="D")]),
    ])
    scenes, warnings = to_scenes(plan, get_library(), max_shots=3)
    a, b = scenes[0].shots
    assert a.direction.shot_size is None and a.direction.angle == "low"
    assert [m.id for m in a.direction.camera_moves] == ["dolly_in"] and a.duration == 10
    assert a.continuity == "cut" and b.continuity == "continue" and b.duration == 3  # a scene opens on a cut
    # one feeling per cast member, as an @handle; unknown emotions are dropped
    assert [(c.who, c.emotion) for c in b.direction.cast_emotions] == [("@maya", "grief")]
    assert [s.action for s in scenes[1].shots] == ["C"]
    assert warnings == ["the plan had more than 3 shots; the rest were left out", "unknown camera moves dropped: warp_drive"]


def test_mock_plan_uses_the_cast():
    plan = mock_plan("One. Two.\n\nThree.", ["maya"], 12)
    assert [[s.action for s in sc.shots] for sc in plan.scenes] == [["@maya One.", "Two."], ["@maya Three."]]


def test_document_rules():
    with pytest.raises(ValidationError, match="unique"):
        CinemaDocument.model_validate({"scenes": [{"id": "caaa", "shots": [{"id": "caaa"}]}]})
    with pytest.raises(ValidationError):
        CinemaDocument.model_validate({"scenes": [{"shots": [{"id": "Bad Id"}]}]})
    settings = Settings(direction=ShotDirection(genre="noir", lighting="low_key"))
    shot = Shot(direction=ShotDirection(lighting="golden_hour"))
    assert merged_direction(settings, shot) == {"genre": "noir", "lighting": "golden_hour", "camera_moves": []}


def test_join_graph_mixes_cuts_and_fades():
    graph, total = join_graph([4.0, 3.0, 2.0], ["cut", "fade", "cut"], 0.5)
    assert "xfade=transition=fade:duration=0.500:offset=3.500" in graph
    assert "[v1][a1][p2v][p2a]concat=n=2:v=1:a=1" in graph
    assert total == pytest.approx(4.0 + 3.0 - 0.5 + 2.0)
    # a fade never takes more than half of either clip (0.6 s is 14 frames, so 7 at most)
    graph, total = join_graph([0.6, 0.6], ["cut", "fade"], 2.0)
    assert "duration=0.292" in graph and total == pytest.approx(21 / 24)


def test_the_cut_is_planned_in_whole_frames():
    from studio.media.timeline import FPS, plan, to_frames

    assert [to_frames(s) for s in (0.5, 1 / 48, 3 / 48, 2.99)] == [12, 1, 2, 72]  # halves round up, as in JS
    p = plan([4.0, 3.0, 0.1, 2.0], ["cut", "fade", "cut", "fade"], 0.5)
    assert [(s.start, s.frames, s.overlap) for s in p.segments] == [(0, 96, 0), (84, 72, 12), (156, 5, 0),
                                                                     (159, 48, 2)]
    assert p.frames == 207 and p.seconds == pytest.approx(207 / FPS)


def test_title_card_text_wraps_inside_the_card():
    from studio.media.timeline import CARD_TEXT_WIDTH, _font, wrap

    font, width = _font(65), 1280 * CARD_TEXT_WIDTH
    text = wrap("The keeper climbed the stairs one last time, and the lamp went dark over the harbour", font, width)
    lines = text.split("\n")
    assert len(lines) > 1 and all(font.getlength(line) <= width for line in lines)
    assert wrap("Short\nTwo lines", font, width) == "Short\nTwo lines"  # typed line breaks stay
    split = wrap("A" * 80, font, 300).split("\n")  # one word wider than the card is split between letters
    assert len(split) > 1 and all(font.getlength(line) <= 300 for line in split)
