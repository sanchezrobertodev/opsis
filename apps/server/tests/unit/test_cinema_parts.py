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
            PlannedShot(action="B", shot_size="close_up", duration=1, continuity="continue"),
        ]),
        PlannedScene(heading="EXT. ROAD", summary="t", shots=[PlannedShot(action="C"), PlannedShot(action="D")]),
    ])
    scenes, warnings = to_scenes(plan, get_library(), max_shots=3)
    a, b = scenes[0].shots
    assert a.direction.shot_size is None and a.direction.angle == "low"
    assert [m.id for m in a.direction.camera_moves] == ["dolly_in"] and a.duration == 10
    assert a.continuity == "cut" and b.continuity == "continue" and b.duration == 3  # a scene opens on a cut
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
    assert "[v1][a1][2:v][2:a]concat=n=2:v=1:a=1" in graph
    assert total == pytest.approx(4.0 + 3.0 - 0.5 + 2.0)
    # a fade never takes more than half of either clip
    graph, total = join_graph([0.6, 0.6], ["cut", "fade"], 2.0)
    assert "duration=0.300" in graph and total == pytest.approx(0.9)
