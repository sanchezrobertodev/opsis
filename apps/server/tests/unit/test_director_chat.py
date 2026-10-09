"""The AI Director chat's edits: applied by id, keeping what Claude can't see, and shown as a diff."""

from __future__ import annotations

from studio.cinema.director_chat import DirectorEdit, DirectorShot, apply_edits, diff, mock_reply
from studio.cinema.document import CinemaDocument, Scene, Shot, ShotLine
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


def planned(shot: Shot, **change) -> DirectorShot:
    d = shot.direction
    base = dict(action=shot.action, shot_size=d.shot_size, angle=d.angle, camera_moves=[m.id for m in d.camera_moves],
                duration=shot.duration, continuity=shot.continuity,
                lines=[{"who": x.who, "text": x.text} for x in shot.lines])
    return DirectorShot(**{**base, **change})


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
                     shot=DirectorShot(action="Gulls.", shot_size="nope", camera_moves=["warp_9"], duration=40)),
        DirectorEdit(op="move_shot", shot_id="sfour", scene_id="cdock", after_id=None),
        DirectorEdit(op="remove_shot", shot_id="stwo"),
        DirectorEdit(op="remove_scene", scene_id="csea"),
        DirectorEdit(op="add_scene", after_id="cdock", heading="INT. CABIN - NIGHT",
                     shots=[DirectorShot(action="A lamp swings.")]),
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
                                                  shot=DirectorShot(action="Rain.", continuity="continue"))], LIB)
    doc = CinemaDocument(scenes=after)
    assert doc.scenes[1].shots[1].continuity == "continue"


def test_the_diff_names_shots_as_they_will_read():
    before = board()
    after, _ = apply_edits(before, [
        DirectorEdit(op="update_shot", shot_id="sone", shot=planned(before[0].shots[0], shot_size="close_up",
                                                                    action="Fog, thick.")),
        DirectorEdit(op="move_shot", shot_id="sthree", scene_id="cdock", after_id=None),
        DirectorEdit(op="add_shot", scene_id="csea", after_id="sfour", shot=DirectorShot(action="Rain.", duration=4)),
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


def chained() -> list[Scene]:
    return [Scene(id="cdock", shots=[Shot(id="sone", action="A."), Shot(id="stwo", action="B.", continuity="continue"),
                                     Shot(id="sthree", action="C.", continuity="continue"), Shot(id="sfour", action="D.")])]


def test_a_shot_whose_predecessor_changed_stops_continuing():
    # removing shot 2: shot 3 was written to pick up from shot 2's last frame, not shot 1's
    after, _ = apply_edits(chained(), [DirectorEdit(op="remove_shot", shot_id="stwo")], LIB)
    assert [(x.id, x.continuity) for x in after[0].shots] == [("sone", "cut"), ("sthree", "cut"), ("sfour", "cut")]
    # moving a continuing shot elsewhere; and the shot that followed it now follows another
    after, _ = apply_edits(chained(), [DirectorEdit(op="move_shot", shot_id="stwo", after_id="sfour")], LIB)
    assert [(x.id, x.continuity) for x in after[0].shots] == [
        ("sone", "cut"), ("sthree", "cut"), ("sfour", "cut"), ("stwo", "cut")]
    # restating a shot leaves its continuity alone while its predecessor stays
    board_ = chained()
    after, _ = apply_edits(board_, [DirectorEdit(op="update_shot", shot_id="sthree",
                                                 shot=planned(board_[0].shots[2], action="C, again."))], LIB)
    assert after[0].shots[2].continuity == "continue"


def test_an_edit_can_choose_to_continue():
    board_ = chained()
    after, _ = apply_edits(board_, [
        DirectorEdit(op="add_shot", scene_id="cdock", after_id="sone", shot=DirectorShot(action="Z.", continuity="continue")),
        DirectorEdit(op="update_shot", shot_id="sfour", shot=planned(board_[0].shots[3], continuity="continue")),
    ], LIB)
    # the new shot continues from shot 1, and shot 2 (now after it) no longer follows shot 1, so it cuts
    assert [x.continuity for x in after[0].shots] == ["cut", "continue", "cut", "continue", "continue"]


def test_an_explicit_five_seconds_is_kept():
    board_ = board()
    two = board_[0].shots[1]  # no length of its own
    after, _ = apply_edits(board_, [DirectorEdit(op="update_shot", shot_id="stwo", shot=planned(two, duration=5))], LIB)
    assert after[0].shots[1].duration == 5
    new, _ = apply_edits(board_, [DirectorEdit(op="add_shot", scene_id="csea", shot=DirectorShot(action="X."))], LIB)
    assert new[1].shots[0].duration is None  # no length: the model's default


def test_edits_that_break_a_limit_are_not_applied():
    full = [Scene(id="cbig", shots=[Shot(action=f"Shot {i}.") for i in range(60)])]
    after, warnings = apply_edits(full, [DirectorEdit(op="add_shot", scene_id="cbig", shot=DirectorShot(action="One more."))],
                                  LIB)
    assert after is full and len(after[0].shots) == 60
    assert any("weren't applied" in w for w in warnings)


async def test_long_storyboards_and_histories_are_bounded(monkeypatch):
    import pytest

    from studio.cinema import director_chat as dc
    from studio.llm.client import input_ceiling

    assert input_ceiling("s" * 300, "p" * 30000) == 10100 + 2000  # grows with what's sent, not a fixed guess
    sent = {}

    async def fake_structured(session, user_id, **kw):
        sent.update(kw)
        from studio.llm.client import LLMResult
        return LLMResult(output=kw["mock"](), model="m", mock=True)

    monkeypatch.setattr(dc, "structured", fake_structured)
    doc = CinemaDocument(scenes=board())
    history = [dc.Turn(role="user", text=f"turn {i} " + "x" * 3000) for i in range(12)]
    await dc.revise(None, None, doc=doc, message="make shot 1 tighter", history=history, cast=[])
    assert "turn 11" in sent["prompt"] and "turn 6" not in sent["prompt"]  # the newest turns that fit
    monkeypatch.setattr(dc, "MAX_PROMPT_CHARS", 500)
    with pytest.raises(dc.DirectorTooLong):
        await dc.revise(None, None, doc=doc, message="hi", history=[], cast=[])


def test_claudes_flat_reply_becomes_the_edits_the_storyboard_applies():
    from studio.cinema.director_chat import WireEdit, WireReply, WireShot, _from_wire

    shot = WireShot(action="Gulls.", shot_size="", angle="high", camera_moves=[], duration=0, continuity="cut",
                    lines=[], emotions=[])
    reply = _from_wire(WireReply(reply="ok", edits=[
        WireEdit(op="update_shot", shot_id="sone", scene_id="", after_id="", heading="", summary="", shots=[shot]),
        WireEdit(op="remove_shot", shot_id="stwo", scene_id="", after_id="", heading="", summary="", shots=[]),
        WireEdit(op="add_scene", shot_id="", scene_id="", after_id="cdock", heading="EXT. PIER", summary="",
                 shots=[shot, shot]),
    ]))
    update, remove, scene = reply.edits
    assert update.shot.shot_size is None and update.shot.angle == "high" and update.shot.duration is None
    assert update.scene_id is None and update.after_id is None and update.heading is None and update.shots == []
    assert remove.shot is None and remove.shot_id == "stwo"
    assert scene.shot is None and len(scene.shots) == 2 and scene.heading == "EXT. PIER" and scene.summary == ""


def test_a_restated_scene_can_clear_its_heading():
    from studio.cinema.director_chat import WireEdit, WireReply, _from_wire

    reply = _from_wire(WireReply(reply="ok", edits=[WireEdit(op="update_scene", shot_id="", scene_id="cdock",
                                                             after_id="", heading="", summary="Dawn at the dock.",
                                                             shots=[])]))
    scene = apply_edits(board(), reply.edits, LIB)[0][0]
    assert board()[0].heading == "EXT. DOCK - DAWN"
    assert scene.heading == "" and scene.summary == "Dawn at the dock."
