"""Script to shot plan with Claude: scenes of shots, each one continuous camera setup, in the director's own
vocabulary (shot sizes, angles and camera moves by id), with the cast as @mentions. People who recur but aren't
in the cast come back as proposed characters (a handle, a name and a look), so the editor can save them and keep
their faces the same from shot to shot.

The plan is a proposal; the editor shows it and the user applies it to the project document. Unknown ids
are dropped rather than trusted, durations are clamped, and nothing here writes to the project.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.cinema.document import Scene, Shot, ShotLine
from studio.config import get_settings
from studio.director.compiler import (
    MAX_CAST_EMOTIONS,
    MAX_LINES,
    CastEmotion,
    MoveChoice,
    ShotDirection,
)
from studio.director.elements import MENTION
from studio.director.library import DirectorLibrary, get_library
from studio.llm.client import LLMResult, structured

MAX_SHOTS = 40


class PlannedEmotion(BaseModel):
    who: str = Field(description="A cast member's @handle")
    emotion: str = Field(description="An emotion id from the list")


class PlannedLine(BaseModel):
    who: str = Field(description="The speaker's @handle, or a name for anyone else")
    text: str = Field(description="The words, as spoken")


class PlannedShot(BaseModel):
    action: str = Field(description="What the camera sees in this one continuous shot, present tense, concrete. "
                                    "Name cast members by their @handle.")
    shot_size: str | None = Field(None, description="A shot size id from the list")
    angle: str | None = Field(None, description="An angle id from the list")
    camera_moves: list[str] = Field(default_factory=list, description="Up to two camera move ids from the list")
    duration: int = Field(5, description="Seconds, 3 to 10")
    continuity: Literal["cut", "continue"] = Field(
        "cut", description="continue only when this shot picks up exactly where the previous shot's last frame ends")
    lines: list[PlannedLine] = Field(default_factory=list, description="Lines spoken in this shot, in order")
    emotions: list[PlannedEmotion] = Field(
        default_factory=list, description="How each cast member in the shot feels, when it matters")


class PlannedScene(BaseModel):
    heading: str = Field(description="Scene heading, e.g. EXT. HARBOUR - DAWN")
    summary: str = Field(description="One sentence on what the scene is for")
    shots: list[PlannedShot]


class PlannedCharacter(BaseModel):
    handle: str = Field(description="A short lowercase handle, letters only, used as @handle in the shots")
    name: str = Field(description="What to call them, e.g. The Keeper or Mara")
    look: str = Field(description="How they look, the same in every shot: age, build, face, hair and clothes; "
                                  "concrete and visual, no personality")


class ShotPlan(BaseModel):
    scenes: list[PlannedScene]
    characters: list[PlannedCharacter] = Field(
        default_factory=list, description="People who appear in more than one shot and aren't in the cast")


# the director chat can't add characters, so there an invented handle would reach the video model as "@name"
NO_CAST = "No cast is saved: describe every person in words and never write an @handle."
NEW_PEOPLE = ("Anyone who appears in more than one shot and isn't in the cast: list them under characters with a "
              "new @handle, a name and a look, and write them into the shots by that @handle (not by their looks). "
              "Describe everyone else in words.")


class ProposedCharacter(BaseModel):
    handle: str
    name: str
    description: str

SYSTEM = """You are a film director breaking a script into a shot list for AI video generation.

Each shot is one continuous camera setup of 3 to 10 seconds that a video model renders on its own. For every shot:
- Describe only what the camera sees: who, what they do in order, where, and how the frame is composed.
- Refer to cast members by their @handle exactly as listed; don't describe their appearance (their reference
  pictures supply it). Describe anyone else briefly the same way every time they appear.
- Pick the shot size, angle and camera moves from the id lists given; use null or [] when nothing fits.
- When a cast member's feeling drives the shot, give it as an emotion id for their @handle.
- Use continuity "continue" only when the shot starts exactly where the previous one ended; otherwise "cut".
- Vary framing and keep screen direction consistent within a scene.
Group shots into scenes with a heading and a one-line summary. Stay within the shot budget."""


def _vocabulary(lib: DirectorLibrary) -> str:
    return "\n".join([
        "Shot sizes: " + ", ".join(f"{i.id} ({i.label})" for i in lib.shot_sizes),
        "Angles: " + ", ".join(f"{i.id} ({i.label})" for i in lib.angles),
        "Camera moves: " + ", ".join(f"{m.id} ({m.label})" for m in lib.moves),
        "Emotions: " + ", ".join(f"{e.id} ({e.label})" for e in lib.emotions),
    ])


async def plan_shots(session: AsyncSession, user_id: uuid.UUID, *, script: str, logline: str = "",
                     cast: list[tuple[str, str]] | None = None, notes: str = "",
                     max_shots: int = 12,
                     ) -> tuple[LLMResult[ShotPlan], list[Scene], list[ProposedCharacter], list[str]]:
    """Returns the raw result, the plan as document scenes, the new characters it proposes, and warnings about
    anything dropped."""
    lib = get_library()
    max_shots = max(1, min(max_shots, MAX_SHOTS))
    cast = cast or []
    prompt = "\n".join(line for line in [
        f"Logline: {logline}" if logline else "",
        "Cast (use these handles): " + "; ".join(f"@{slug}: {desc}" for slug, desc in cast) if cast else "",
        NEW_PEOPLE,
        f"Shot budget: at most {max_shots} shots in total.",
        f"Director's notes: {notes}" if notes else "",
        _vocabulary(lib),
        f"\nScript:\n{script or logline}",
    ] if line)
    result = await structured(
        session, user_id, purpose="cinema.plan", model=get_settings().llm_smart_model, system=SYSTEM, prompt=prompt,
        output=ShotPlan, mock=lambda: mock_plan(script or logline, [slug for slug, _ in cast], max_shots),
        effort="medium", max_tokens=16000)
    scenes, warnings = to_scenes(result.output, lib, max_shots)
    characters = new_characters(result.output.characters, scenes, {slug for slug, _ in cast})
    scenes = [_unmention(s, {slug for slug, _ in cast} | {c.handle for c in characters}) for s in scenes]
    return result, scenes, characters, warnings


def new_characters(planned: list[PlannedCharacter], scenes: list[Scene], cast: set[str]) -> list[ProposedCharacter]:
    """The proposed characters worth saving: a usable handle that isn't a cast member's, mentioned in a shot."""
    used = {m.lower() for s in scenes for shot in s.shots for text in _texts(shot) for m in MENTION.findall(text)}
    out: dict[str, ProposedCharacter] = {}
    for c in planned:
        handle = re.sub(r"[^a-z0-9-]", "", c.handle.strip().lstrip("@").lower())[:40]
        if handle and handle not in cast and handle in used and handle not in out and c.name.strip():
            out[handle] = ProposedCharacter(handle=handle, name=c.name.strip()[:80], description=c.look.strip()[:600])
    return list(out.values())


def _texts(shot: Shot) -> list[str]:
    return [shot.action, *(x.who for x in shot.lines), *(e.who for e in shot.direction.cast_emotions)]


def _unmention(scene: Scene, known: set[str]) -> Scene:
    """An @handle nobody has (neither the cast nor a proposed character) becomes a plain word."""
    def fix(text: str) -> str:
        return MENTION.sub(lambda m: m.group(0) if m.group(1).lower() in known else m.group(1), text)

    shots = []
    for shot in scene.shots:
        emotions = [e for e in shot.direction.cast_emotions if e.who.lstrip("@").lower() in known]
        shots.append(shot.model_copy(update={
            "action": fix(shot.action), "lines": [x.model_copy(update={"who": fix(x.who)}) for x in shot.lines],
            "direction": shot.direction.model_copy(update={"cast_emotions": emotions})}))
    return scene.model_copy(update={"shots": shots})


def to_scenes(plan: ShotPlan, lib: DirectorLibrary, max_shots: int) -> tuple[list[Scene], list[str]]:
    warnings: list[str] = []
    scenes: list[Scene] = []
    budget = max_shots
    for ps in plan.scenes:
        shots = []
        for p in ps.shots:
            if budget == 0:
                warnings.append(f"the plan had more than {max_shots} shots; the rest were left out")
                break
            budget -= 1
            moves = [m for m in p.camera_moves if lib.move(m)][:2]
            dropped = [m for m in p.camera_moves if not lib.move(m)]
            if dropped:
                warnings.append(f"unknown camera moves dropped: {', '.join(dropped)}")
            feelings: dict[str, CastEmotion] = {}
            for e in p.emotions:
                who = "@" + e.who.strip().lstrip("@").lower()
                if lib.look("emotions", e.emotion) and len(who) > 1:
                    feelings.setdefault(who, CastEmotion(who=who, emotion=e.emotion))
            direction = ShotDirection(
                shot_size=p.shot_size if lib.look("shot_sizes", p.shot_size) else None,
                angle=p.angle if lib.look("angles", p.angle) else None,
                camera_moves=[MoveChoice(id=m) for m in moves],
                cast_emotions=list(feelings.values())[:MAX_CAST_EMOTIONS])
            shots.append(Shot(action=p.action.strip()[:2000], duration=max(3, min(10, p.duration)),
                              direction=direction,
                              lines=[ShotLine(who=x.who.strip()[:60], text=x.text.strip()[:500])
                                     for x in p.lines if x.who.strip() and x.text.strip()][:MAX_LINES],
                              continuity="continue" if shots and p.continuity == "continue" else "cut"))
        if shots:
            scenes.append(Scene(heading=ps.heading.strip()[:200], summary=ps.summary.strip()[:1000], shots=shots))
        if budget == 0 and ps is not plan.scenes[-1]:
            break
    return scenes, sorted(set(warnings))


def mock_plan(text: str, cast: list[str], max_shots: int) -> ShotPlan:
    """LLM_MODE=mock: one scene per paragraph, one shot per sentence, framings in rotation."""
    sizes = ["wide", "medium", "close_up"]
    moves = [["dolly_in"], [], ["pan_right"]]
    scenes, n = [], 0
    for i, para in enumerate([p for p in re.split(r"\n\s*\n", text) if p.strip()] or [text or "A quiet morning."]):
        shots = []
        for j, sentence in enumerate(s for s in re.split(r"(?<=[.!?])\s+", para.strip()) if s):
            if n >= max_shots:
                break
            who = f"@{cast[0]} " if cast and j == 0 else "@lead " if not cast and j == 0 else ""
            shots.append(PlannedShot(action=f"{who}{sentence}".strip(), shot_size=sizes[j % 3],
                                     camera_moves=moves[j % 3], duration=5, continuity="continue" if j else "cut"))
            n += 1
        if shots:
            scenes.append(PlannedScene(heading=f"SCENE {i + 1}", summary=para.strip()[:120], shots=shots))
    lead = [] if cast else [PlannedCharacter(handle="lead", name="The lead", look="a woman in her thirties, short "
                                             "dark hair, a green raincoat over a grey sweater")]
    return ShotPlan(scenes=scenes, characters=lead)
