"""The AI Director chat's edits: applied by id, keeping what Claude can't see, and shown as a diff."""

from __future__ import annotations

from studio.cinema.director_chat import DirectorEdit, apply_edits, diff, mock_reply
from studio.cinema.document import CinemaDocument, Scene, Shot, ShotLine
from studio.cinema.planner import PlannedShot
from studio.director.compiler import MoveChoice, ShotDirection
from studio.director.library import get_library

LIB = get_library()


def board() -> list[Scene]:
    return [
        Scene(id="cdock", heading="EXT. DOCK - DAWN", shots=[
            Shot(id="sone", action="Fog on the water.", duration=5, direction=ShotDirection(
                shot_size="wide", palette="teal_orange", camera_moves=[MoveChoice(id="pan_right", speed="slow")]),
                 hero_asset_id="h1", take_asset_id="t1", trim_in=0.5),
            Shot(id="stwo", action="@mara ties the rope.", continuity="continue",
                 lines=[ShotLine(who="@mara", text="Almost.", voice_id="v9")]),
            Shot(id="sthree", action="The boat drifts off.", duration=12),
        ]),
        Scene(id="csea", heading="EXT. SEA - DAY", shots=[Shot(id="sfour", action="Open water.")]),
    ]


def planned(shot: Shot, **change) -> PlannedShot:
    d = shot.direction
    base = dict(action=shot.action, shot_size=d.shot_size, angle=d.angle, camera_moves=[m.id for m in d.camera_moves],
                duration=shot.duration or 5, continuity=shot.continuity,
                lines=[{"who": x.who, "text": x.text} for x in shot.lines])
    return PlannedShot(**{**base, **change})


def test_an_update_keeps_picks_trims_look_and_voices():
    before = board()
    one, two, three = before[0].shots
    after, warnings = apply_edits(before, [
        DirectorEdit(op="update_shot", shot_id="sone", shot=planned(one, shot_size="close_up",
                                                                    camera_moves=["pan_right", "dolly_in"])),
        DirectorEdit(op="update_shot", shot_id="stwo", shot=planned(two, action="@mara ties the rope fast.")),
        DirectorEdit(op="update_shot", shot_id="sthree", shot=planned(three)),  # restated as it was
    ], LIB)
    assert warnings == []
    s1, s2, s3 = after[0].shots
    assert (s1.hero_asset_id, s1.take_asset_id, s1.trim_in, s1.direction.palette) == ("h1", "t1", 0.5, "teal_orange")
    assert s1.direction.shot_size == "close_up"
    assert [(m.id, m.speed) for m in s1.direction.camera_moves] == [("pan_right", "slow"), ("dolly_in", "normal")]
    assert s2.lines[0].voice_id == "v9" and s2.continuity == "continue"
    assert s2.duration is None  # the model's default stays the default
    assert s3.duration == 12  # longer than the planner's range, but untouched
    assert before[0].shots[0].direction.shot_size == "wide"  # the input isn't changed


def test_add_move_remove_and_scenes():
    after, warnings = apply_edits(board(), [
        DirectorEdit(op="add_shot", scene_id="cdock", after_id="sone",
                     shot=PlannedShot(action="Gulls.", shot_size="nope", camera_moves=["warp_9"], duration=40)),
        DirectorEdit(op="move_shot", shot_id="sfour", scene_id="cdock", after_id=None),
        DirectorEdit(op="remove_shot", shot_id="stwo"),
        DirectorEdit(op="remove_scene", scene_id="csea"),
        DirectorEdit(op="add_scene", after_id="cdock", heading="INT. CABIN - NIGHT",
                     shots=[PlannedShot(action="A lamp swings.")]),
        DirectorEdit(op="update_scene", scene_id="cdock", summary="Leaving."),
        DirectorEdit(op="remove_shot", shot_id="sghost"),
    ], LIB)
    assert [s.heading for s in after] == ["EXT. DOCK - DAWN", "INT. CABIN - NIGHT"]
    dock = after[0]
    assert dock.summary == "Leaving."
    assert [x.action for x in dock.shots] == ["Open water.", "Fog on the water.", "Gulls.", "The boat drifts off."]
    gulls = dock.shots[2]
    assert gulls.direction.shot_size is None and gulls.direction.camera_moves == [] and gulls.duration == 10
    assert "skipped remove shot: no shot sghost" in warnings
    assert any("warp_9" in w for w in warnings)


def test_a_shot_that_now_opens_its_scene_is_a_cut():
    after, _ = apply_edits(board(), [DirectorEdit(op="remove_shot", shot_id="sone")], LIB)
    assert after[0].shots[0].id == "stwo" and after[0].shots[0].continuity == "cut"


def test_the_result_is_still_a_valid_document():
    after, _ = apply_edits(board(), [DirectorEdit(op="add_shot", scene_id="csea", after_id="sfour",
                                                  shot=PlannedShot(action="Rain.", continuity="continue"))], LIB)
    doc = CinemaDocument(scenes=after)
    assert doc.scenes[1].shots[1].continuity == "continue"


def test_the_diff_names_shots_as_they_will_read():
    before = board()
    after, _ = apply_edits(before, [
        DirectorEdit(op="update_shot", shot_id="sone", shot=planned(before[0].shots[0], shot_size="close_up",
                                                                    action="Fog, thick.")),
        DirectorEdit(op="move_shot", shot_id="sthree", scene_id="cdock", after_id=None),
        DirectorEdit(op="add_shot", scene_id="csea", after_id="sfour", shot=PlannedShot(action="Rain.", duration=4)),
    ], LIB)
    changes = diff(before, after)
    got = {(c["kind"], c["label"]): {f["field"]: (f["before"], f["after"]) for f in c["fields"]} for c in changes}
    assert got[("moved", "Shot 1 (was 3)")] == {}
    assert got[("changed", "Shot 2")] == {"action": ("Fog on the water.", "Fog, thick."),
                                          "shot_size": ("wide", "close_up")}
    assert got[("added", "Shot 5")] == {"action": (None, "Rain."), "duration": (None, 4), "continuity": (None, "cut")}
    assert len(changes) == 3  # the others kept their order, so they didn't move

    gone, _ = apply_edits(before, [DirectorEdit(op="remove_shot", shot_id="stwo"),
                                   DirectorEdit(op="remove_scene", scene_id="csea")], LIB)
    assert [(c["kind"], c["label"], c.get("shot_id")) for c in diff(before, gone)] == [
        ("removed", "EXT. SEA - DAY", None), ("removed", "Shot 2", "stwo"), ("removed", "Shot 4", "sfour")]


def test_nothing_changed_is_an_empty_diff():
    assert diff(board(), board()) == []


def test_the_mock_understands_shot_numbers():
    doc = CinemaDocument(scenes=board())
    assert mock_reply(doc, "remove shot 3").edits[0].model_dump(include={"op", "shot_id"}) == {
        "op": "remove_shot", "shot_id": "sthree"}
    add = mock_reply(doc, "add a beat after shot 2").edits[0]
    assert (add.op, add.scene_id, add.after_id) == ("add_shot", "cdock", "stwo")
    tight = mock_reply(doc, "make shot 4 tighter").edits[0]
    assert tight.shot_id == "sfour" and tight.shot.shot_size == "close_up"
    assert mock_reply(CinemaDocument(), "anything").edits == []
