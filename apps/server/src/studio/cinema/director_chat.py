"""The AI Director chat: the user asks for changes to the storyboard in words, Claude answers with edits by shot
and scene id, and the studio turns them into a proposed set of scenes plus a diff against the current ones.

Nothing is saved here. Edits only touch what Claude can see (the action, framing, moves, length, continuity, lines
and feelings); a shot's picks, trims and the rest of its look are kept. Unknown ids are skipped with a warning.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.cinema.document import CinemaDocument, Scene, Shot, ShotLine
from studio.cinema.planner import (
    MAX_SHOTS,
    NO_CAST,
    PlannedEmotion,
    PlannedLine,
    PlannedScene,
    PlannedShot,
    ShotPlan,
    _vocabulary,
    to_scenes,
)
from studio.config import get_settings
from studio.director.compiler import MoveChoice
from studio.director.library import DirectorLibrary, get_library
from studio.llm.client import LLMResult, structured

MAX_TURNS = 12  # earlier turns of the conversation sent back to Claude
MAX_HISTORY_CHARS = 16000  # and at most this much of them, newest first
MAX_PROMPT_CHARS = 200_000  # a storyboard longer than this is refused rather than sent


class DirectorTooLong(ValueError):
    pass


class DirectorShot(PlannedShot):
    duration: int | None = Field(None, description="Seconds, 3 to 10; null leaves a shot's length as it is (a new "
                                                   "shot gets the model's default)")


class DirectorEdit(BaseModel):
    op: Literal["update_shot", "add_shot", "remove_shot", "move_shot", "update_scene", "add_scene", "remove_scene"]
    shot_id: str | None = Field(None, description="The shot to update, remove or move")
    scene_id: str | None = Field(None, description="The scene to update or remove, or where add_shot/move_shot "
                                                    "puts the shot")
    after_id: str | None = Field(None, description="add_shot/move_shot: the shot it goes after (null: the scene's "
                                                   "start). add_scene: the scene it goes after (null: the start)")
    shot: DirectorShot | None = Field(None, description="update_shot: the whole shot as it should read now. "
                                                       "add_shot: the new shot")
    heading: str | None = Field(None, description="update_scene/add_scene")
    summary: str | None = Field(None, description="update_scene/add_scene")
    shots: list[DirectorShot] = Field(default_factory=list, description="add_scene: its shots")


class DirectorReply(BaseModel):
    reply: str = Field(description="One to three sentences to the user: what you changed and why, or a question "
                                   "when the request is unclear")
    edits: list[DirectorEdit] = Field(default_factory=list, description="The changes, applied in order; none when "
                                                                        "you are only answering or asking")


# What Claude fills in. The API compiles the output schema into a grammar, and nullable nested objects plus many
# optional fields make it "too complex": so every field is required and nothing is nullable ("" and 0 stand for
# "none"), and one `shots` list carries the single shot of update_shot/add_shot as well as add_scene's shots.
class WireShot(BaseModel):
    action: str = Field(description=PlannedShot.model_fields["action"].description)
    shot_size: str = Field(description="A shot size id from the list, or \"\" when none fits")
    angle: str = Field(description="An angle id from the list, or \"\" when none fits")
    camera_moves: list[str] = Field(description="Up to two camera move ids from the list")
    duration: int = Field(description="Seconds, 3 to 10; 0 leaves a shot's length as it is (a new shot gets the "
                                      "model's default)")
    continuity: Literal["cut", "continue"] = Field(description=PlannedShot.model_fields["continuity"].description)
    lines: list[PlannedLine] = Field(description="Lines spoken in this shot, in order")
    emotions: list[PlannedEmotion] = Field(description="How each cast member in the shot feels, when it matters")


class WireEdit(BaseModel):
    op: Literal["update_shot", "add_shot", "remove_shot", "move_shot", "update_scene", "add_scene", "remove_scene"]
    shot_id: str = Field(description="The shot to update, remove or move; \"\" otherwise")
    scene_id: str = Field(description="The scene to update or remove, or where add_shot/move_shot puts the shot; "
                                      "\"\" otherwise")
    after_id: str = Field(description="add_shot/move_shot: the shot it goes after (\"\": the scene's start). "
                                      "add_scene: the scene it goes after (\"\": the start)")
    heading: str = Field(description="update_scene/add_scene: the heading (\"\" keeps it as it is)")
    summary: str = Field(description="update_scene/add_scene: the summary (\"\" keeps it as it is)")
    shots: list[WireShot] = Field(description="update_shot: the whole shot as it should read now; add_shot: the new "
                                              "shot; add_scene: its shots; empty for the other edits")


class WireReply(BaseModel):
    reply: str = Field(description=DirectorReply.model_fields["reply"].description)
    edits: list[WireEdit] = Field(description=DirectorReply.model_fields["edits"].description)


def _from_wire(w: WireReply) -> DirectorReply:
    def shot(x: WireShot) -> DirectorShot:
        return DirectorShot(**x.model_dump(exclude={"shot_size", "angle", "duration"}), shot_size=x.shot_size or None,
                            angle=x.angle or None, duration=x.duration or None)

    edits = []
    for e in w.edits:
        one = e.op in ("update_shot", "add_shot")
        edits.append(DirectorEdit(
            op=e.op, shot_id=e.shot_id or None, scene_id=e.scene_id or None, after_id=e.after_id or None,
            heading=e.heading or None, summary=e.summary or None,
            shot=shot(e.shots[0]) if one and e.shots else None,
            shots=[shot(x) for x in e.shots] if e.op == "add_scene" else []))
    return DirectorReply(reply=w.reply, edits=edits)


class Turn(BaseModel):
    role: Literal["user", "director"]
    text: str = Field(max_length=4000)


SYSTEM = """You are the film's director, revising its storyboard with the user in a chat.

The storyboard is listed with scene and shot ids. Answer each message with a short reply and the edits that carry
it out, and change only what the user asked for (plus what a change forces, like continuity after a removed shot).
- update_shot restates the whole shot as it should read (as the one item in `shots`); keep the parts you aren't
  changing as they are.
- Each shot is one continuous camera setup of 3 to 10 seconds. Describe only what the camera sees, name cast
  members by their @handle and don't describe their looks.
- Pick shot sizes, angles, camera moves and emotions from the id lists; use "" or [] when nothing fits.
- continuity "continue" only when a shot starts exactly where the previous one ends; otherwise "cut".
- Refer to shots by number in your reply ("shot 4"), never by id.
If the request is unclear or asks for something a storyboard can't hold, ask instead and return no edits."""


def _storyboard(doc: CinemaDocument) -> str:
    rows, n = [], 0
    for scene in doc.scenes:
        rows.append(f"Scene [{scene.id}] {scene.heading or '(no heading)'}: {scene.summary}".rstrip(": "))
        for shot in scene.shots:
            n += 1
            d = shot.direction
            framing = ", ".join(x for x in [d.shot_size, d.angle, " + ".join(m.id for m in d.camera_moves),
                                            f"{shot.duration}s" if shot.duration else "default length", shot.continuity] if x)
            rows.append(f"  Shot {n} [{shot.id}] ({framing}) {shot.action}")
            rows += [f"    {line.who}: {line.text}" for line in shot.lines if line.text.strip()]
            rows += [f"    feels: {e.who} {e.emotion}" for e in d.cast_emotions]
    return "\n".join(rows) or "(empty: no scenes yet)"


async def revise(session: AsyncSession, user_id: uuid.UUID, *, doc: CinemaDocument, message: str,
                 history: list[Turn], cast: list[tuple[str, str]]) -> tuple[LLMResult[DirectorReply], list[Scene], list[str]]:
    """Returns Claude's answer, the scenes with its edits applied, and warnings about edits that were skipped."""
    lib = get_library()
    turns, size = [], 0
    for t in reversed(history[-MAX_TURNS:]):
        size += len(t.text)
        if size > MAX_HISTORY_CHARS:
            break
        turns.insert(0, f"{'User' if t.role == 'user' else 'You'}: {t.text}")
    talk = "\n".join(turns)
    prompt = "\n".join(part for part in [
        f"Logline: {doc.logline}" if doc.logline else "",
        "Cast (use these handles): " + "; ".join(f"@{slug}: {desc}" for slug, desc in cast) if cast else NO_CAST,
        _vocabulary(lib),
        f"\nStoryboard:\n{_storyboard(doc)}",
        f"\nThe conversation so far:\n{talk}" if talk else "",
        f"\nUser: {message}",
    ] if part)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise DirectorTooLong("this storyboard is too long for the director to read in one go; shorten some shots' "
                              "actions or lines")
    wire = await structured(
        session, user_id, purpose="cinema.director", model=get_settings().llm_smart_model, system=SYSTEM,
        prompt=prompt, output=WireReply, mock=lambda: mock_reply(doc, message), effort="medium", max_tokens=12000)
    output = wire.output if isinstance(wire.output, DirectorReply) else _from_wire(wire.output)
    result = LLMResult(output=output, model=wire.model, mock=wire.mock, cost_usd=wire.cost_usd, usage=wire.usage)
    scenes, warnings = apply_edits(doc.scenes, result.output.edits, lib)
    return result, scenes, warnings


def _as_planned(p: DirectorShot) -> PlannedShot:
    return PlannedShot(**p.model_dump(exclude={"duration"}), **({"duration": p.duration} if p.duration else {}))


def _planned(p: DirectorShot, lib: DirectorLibrary) -> tuple[Shot, list[str]]:
    """A planned shot as a document shot, through the planner's own checks."""
    scenes, warnings = to_scenes(ShotPlan(scenes=[PlannedScene(heading="", summary="", shots=[_as_planned(p)])]),
                                 lib, 1)
    shot = scenes[0].shots[0]
    # alone in its scene here, the planner would make it a cut; where it lands decides (fixed up after the edits)
    return shot.model_copy(update={"continuity": p.continuity, "duration": shot.duration if p.duration else None}), \
        warnings


def _revised(old: Shot, p: DirectorShot, lib: DirectorLibrary) -> tuple[Shot, list[str]]:
    """The old shot with what Claude can see replaced, keeping its picks, trims and the rest of its look."""
    new, warnings = _planned(p, lib)
    speeds = {m.id: m.speed for m in old.direction.camera_moves}
    direction = old.direction.model_copy(update={
        "shot_size": new.direction.shot_size, "angle": new.direction.angle,
        "camera_moves": [MoveChoice(id=m.id, speed=speeds.get(m.id, m.speed)) for m in new.direction.camera_moves],
        "cast_emotions": new.direction.cast_emotions})
    voices = {(line.who, line.text): line.voice_id for line in old.lines}
    lines = [ShotLine(who=x.who, text=x.text, voice_id=voices.get((x.who, x.text))) for x in new.lines]
    # no length, or the same one, keeps it as it was (even outside the planner's 3-10 s range)
    duration = old.duration if p.duration is None or p.duration == old.duration else new.duration
    return old.model_copy(update={"action": new.action, "duration": duration, "direction": direction,
                                  "lines": lines, "continuity": new.continuity}), warnings


def apply_edits(scenes: list[Scene], edits: list[DirectorEdit], lib: DirectorLibrary) -> tuple[list[Scene], list[str]]:
    out = [s.model_copy(update={"shots": list(s.shots)}) for s in scenes]
    warnings: list[str] = []
    # shots an edit itself made "continue" (a new shot, or one switched to it); any other keeps continuing only
    # while the shot before it is still the one it was written after
    chosen: set[str] = set()

    def scene_of(shot_id: str | None) -> Scene | None:
        return next((s for s in out if any(x.id == shot_id for x in s.shots)), None)

    def place(scene: Scene, shot: Shot, after_id: str | None) -> None:
        at = next((i + 1 for i, x in enumerate(scene.shots) if x.id == after_id), 0 if after_id is None else None)
        if at is None:
            warnings.append(f"a shot went at the end of its scene: the shot it should follow ({after_id}) isn't there")
            at = len(scene.shots)
        scene.shots.insert(at, shot)

    for e in edits:
        if e.op in ("update_shot", "remove_shot", "move_shot"):
            home = scene_of(e.shot_id)
            if home is None:
                warnings.append(f"skipped {e.op.replace('_', ' ')}: no shot {e.shot_id}")
                continue
            i = next(i for i, x in enumerate(home.shots) if x.id == e.shot_id)
            if e.op == "update_shot":
                if e.shot is None:
                    warnings.append("skipped an update with no shot")
                    continue
                old = home.shots[i]
                home.shots[i], w = _revised(old, e.shot, lib)
                warnings += w
                if e.shot.continuity == "continue" and old.continuity != "continue":
                    chosen.add(old.id)
            elif e.op == "remove_shot":
                home.shots.pop(i)
            else:
                dest = next((s for s in out if s.id == (e.scene_id or home.id)), None)
                if dest is None:
                    warnings.append(f"skipped move shot: no scene {e.scene_id}")
                    continue
                place(dest, home.shots.pop(i), e.after_id)
        elif e.op == "add_shot":
            dest = next((s for s in out if s.id == e.scene_id), None) or scene_of(e.after_id)
            if dest is None or e.shot is None:
                warnings.append(f"skipped add shot: no scene {e.scene_id}" if e.shot else "skipped an empty new shot")
                continue
            shot, w = _planned(e.shot, lib)
            warnings += w
            chosen.add(shot.id)
            place(dest, shot, e.after_id)
        elif e.op == "add_scene":
            new, w = to_scenes(ShotPlan(scenes=[PlannedScene(heading=e.heading or "", summary=e.summary or "",
                                                             shots=[_as_planned(x) for x in e.shots])]), lib, MAX_SHOTS)
            warnings += w
            scene = new[0] if new else Scene(heading=(e.heading or "")[:200], summary=(e.summary or "")[:1000])
            scene.shots = [x.model_copy(update={"duration": x.duration if p.duration else None})
                           for x, p in zip(scene.shots, e.shots, strict=False)]
            chosen.update(x.id for x in scene.shots)
            at = next((i + 1 for i, s in enumerate(out) if s.id == e.after_id), 0 if e.after_id is None else len(out))
            out.insert(at, scene)
        else:  # update_scene, remove_scene
            i = next((i for i, s in enumerate(out) if s.id == e.scene_id), None)
            if i is None:
                warnings.append(f"skipped {e.op.replace('_', ' ')}: no scene {e.scene_id}")
            elif e.op == "remove_scene":
                out.pop(i)
            else:
                out[i] = out[i].model_copy(update={
                    "heading": (e.heading if e.heading is not None else out[i].heading)[:200],
                    "summary": (e.summary if e.summary is not None else out[i].summary)[:1000]})

    # a shot continues from the last frame of the shot before it: one that now opens its scene, or follows a
    # different shot than it was written after, would chain from the wrong picture, so it becomes a cut
    before = _predecessors(scenes)
    for s in out:
        prev = None
        for i, shot in enumerate(s.shots):
            if shot.continuity == "continue" and (
                    prev is None or (shot.id not in chosen and before.get(shot.id, prev) != prev)):
                s.shots[i] = shot.model_copy(update={"continuity": "cut"})
            prev = shot.id
    try:
        # from plain data: model instances aren't re-checked, so a 61st shot in a scene would slip through
        CinemaDocument.model_validate({"scenes": [s.model_dump() for s in out]})
    except ValidationError as exc:
        warnings.append(f"the edits weren't applied: the storyboard would break a limit ({exc.errors()[0]['msg']})")
        return scenes, sorted(set(warnings))
    return out, sorted(set(warnings))


def _predecessors(scenes: list[Scene]) -> dict[str, str | None]:
    return {shot.id: (s.shots[i - 1].id if i else None) for s in scenes for i, shot in enumerate(s.shots)}


# ---------------------------------------------------------------- the diff the editor shows

FIELDS = ("action", "shot_size", "angle", "camera_moves", "duration", "continuity", "lines", "feelings")


def _view(shot: Shot) -> dict[str, Any]:
    d = shot.direction
    return {"action": shot.action, "shot_size": d.shot_size, "angle": d.angle,
            "camera_moves": " + ".join(m.id for m in d.camera_moves) or None,
            "duration": shot.duration, "continuity": shot.continuity,
            "lines": "\n".join(f"{x.who}: {x.text}" for x in shot.lines if x.text.strip()) or None,
            "feelings": ", ".join(f"{e.who} {e.emotion}" for e in d.cast_emotions) or None}


def diff(before: list[Scene], after: list[Scene]) -> list[dict[str, Any]]:
    """What changed, in the new story order: scenes and shots added, removed, moved or changed (field by field).
    Shots are numbered as they'll read after the change; removed ones keep their old number."""
    changes: list[dict[str, Any]] = []
    old_shots = {x.id: (n, s, x) for n, (s, x) in enumerate(((s, x) for s in before for x in s.shots), start=1)}
    old_scenes = {s.id: s for s in before}
    old_order = [x.id for s in before for x in s.shots]
    kept = [x.id for s in after for x in s.shots if x.id in old_shots]
    stayed = set(_longest_run(old_order, kept))
    n = 0
    for scene in after:
        if scene.id not in old_scenes:
            changes.append({"kind": "added", "scene_id": scene.id, "label": scene.heading or "New scene",
                            "fields": []})
        else:
            old = old_scenes[scene.id]
            fields = [{"field": f, "before": getattr(old, f), "after": getattr(scene, f)}
                      for f in ("heading", "summary") if getattr(old, f) != getattr(scene, f)]
            if fields:
                changes.append({"kind": "changed", "scene_id": scene.id, "label": scene.heading or "Scene",
                                "fields": fields})
        for shot in scene.shots:
            n += 1
            if shot.id not in old_shots:
                changes.append({"kind": "added", "scene_id": scene.id, "shot_id": shot.id, "label": f"Shot {n}",
                                "fields": [{"field": f, "before": None, "after": v}
                                           for f, v in _view(shot).items() if v not in (None, "")]})
                continue
            was, old_scene, old = old_shots[shot.id]
            fields = [{"field": f, "before": a, "after": b}
                      for f, (a, b) in ((f, (_view(old)[f], _view(shot)[f])) for f in FIELDS) if a != b]
            moved = shot.id not in stayed or old_scene.id != scene.id
            if fields or moved:
                changes.append({"kind": "moved" if moved else "changed", "scene_id": scene.id, "shot_id": shot.id,
                                "label": f"Shot {n}" + (f" (was {was})" if moved else ""), "fields": fields})
    gone_scenes = {s.id for s in before} - {s.id for s in after}
    for scene in before:
        if scene.id in gone_scenes:
            changes.append({"kind": "removed", "scene_id": scene.id, "label": scene.heading or "Scene", "fields": []})
    now = {x.id for s in after for x in s.shots}
    for shot_id, (was, scene, _) in old_shots.items():
        if shot_id not in now:
            changes.append({"kind": "removed", "scene_id": scene.id, "shot_id": shot_id, "label": f"Shot {was}",
                            "fields": []})
    return changes


def _longest_run(order: list[str], kept: list[str]) -> list[str]:
    """The longest subsequence of `kept` that is still in its old order: those shots didn't move."""
    rank = {x: i for i, x in enumerate(order)}
    seq = [rank[x] for x in kept]
    best: list[list[int]] = []
    for v in seq:  # O(n^2) is plenty for 80 shots
        best.append([*max((b for j, b in enumerate(best) if seq[j] < v), key=len, default=[]), v])
    run = max(best, key=len, default=[])
    return [order[i] for i in run]


# ---------------------------------------------------------------- LLM_MODE=mock


def mock_reply(doc: CinemaDocument, message: str) -> DirectorReply:
    """Understands a few plain requests about "shot N": remove it, add one after it, or (anything else) tighten
    it into a close-up with a slow push in."""
    shots = [x for s in doc.scenes for x in s.shots]
    if not shots:
        return DirectorReply(reply="There are no shots yet. Plan the script first, then tell me what to change.")
    m = re.search(r"shot\s+(\d+)", message, re.I)
    n = min(max(int(m.group(1)), 1), len(shots)) if m else 1
    target = shots[n - 1]
    text = message.lower()
    if "remove" in text or "cut shot" in text or "delete" in text:
        return DirectorReply(reply=f"Removed shot {n}.", edits=[DirectorEdit(op="remove_shot", shot_id=target.id)])
    if "add" in text or "insert" in text:
        scene = next(s for s in doc.scenes if target in s.shots)
        new = DirectorShot(action="A quiet insert: hands, a detail, the room breathing.", shot_size="close_up",
                           duration=3)
        return DirectorReply(reply=f"Added a three-second insert after shot {n}.",
                             edits=[DirectorEdit(op="add_shot", scene_id=scene.id, after_id=target.id, shot=new)])
    d = target.direction
    shot = DirectorShot(action=target.action, shot_size="close_up", angle=d.angle, camera_moves=["dolly_in"],
                        duration=target.duration, continuity=target.continuity,
                        lines=[{"who": x.who, "text": x.text} for x in target.lines if x.text.strip()],
                        emotions=[{"who": e.who, "emotion": e.emotion} for e in d.cast_emotions])
    return DirectorReply(reply=f"Shot {n} is now a close-up with a slow push in, to stay with the moment.",
                         edits=[DirectorEdit(op="update_shot", shot_id=target.id, shot=shot)])
