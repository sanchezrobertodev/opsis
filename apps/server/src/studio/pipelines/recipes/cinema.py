"""Cinema Studio runs. The project document is never written here: runs make hero frames and takes (generations
tagged with the project and shot) and the editor picks them into the document.

- cinema_frames: a grid of opening frames per shot, on an image model (an @mentioned character's pictures attach
  when the model takes references).
- cinema_takes: several takes per shot. A shot starts from its picked hero frame, or continues from the last frame
  of the previous shot (its picked take, or the first take this run makes of it). Editing a shot and revising the
  run redoes that shot and the shots chained after it, nothing else. A shot's dialogue goes into the prompt of
  models that voice it themselves; on other models each line is voiced (TTS), the lines are joined into one track,
  and every take is lip-synced to it as a new take.
- cinema_export: the cut. Picked takes and title cards in order, trimmed, joined with cuts or short fades, with
  dialogue, music and SFX tracks mixed under them.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, ValidationError, model_validator

from studio.db.models import StepKind
from studio.director.compiler import DialogueLine, ShotDirection
from studio.director.elements import mentions
from studio.media.speech import CHARS_PER_SECOND
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    bridge_mode,
    edit_mode,
    fit_duration,
    mode_for,
    mode_with_op,
    multi_shot_mode,
    start_frame_mode,
    text_mode,
    with_param,
)
from studio.pipelines.recipes.keyframe import ref_image, ref_mode
from studio.pipelines.recipes.talking import lipsync_mode
from studio.registry.loader import Registry, get_registry
from studio.registry.schema import Mode, ModelSpec
from studio.registry.validate import RequestError
from studio.registry.virtual import resolve as resolve_virtual

KEY = r"^[a-z][a-z0-9]{2,15}$"


def checked_direction(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """A shot's direction as sent, checked like a generation would check it (a bad one is a 422 for the run, not a
    failed take), with dialogue lines still being written (no speaker or words) left out."""
    if not value:
        return value
    d = dict(value)
    lines = d.get("dialogue")
    if lines is not None:
        if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
            raise ValueError("dialogue must be a list of lines, each {who, text}")
        d["dialogue"] = [x for x in lines if str(x.get("text") or "").strip() and str(x.get("who") or "").strip()]
    try:
        ShotDirection.model_validate(d)
    except ValidationError as exc:
        e = exc.errors()[0]
        raise ValueError(f"{e['msg']} at {'.'.join(map(str, e['loc']))}") from None
    return d


Direction = Annotated[dict[str, Any] | None, AfterValidator(checked_direction)]


def frames_key(shot: str, n: int | None = None) -> str:
    return f"frames[{shot}]" if n is None else f"frames[{shot}:{n}]"


def take_key(shot: str, n: int) -> str:
    return f"take[{shot}:{n}]"


def chain_key(shot: str) -> str:
    return f"chain[{shot}]"


def line_key(shot: str, k: int) -> str:
    return f"line[{shot}:{k}]"


def track_key(shot: str) -> str:
    return f"track[{shot}]"


def lip_key(shot: str, n: int) -> str:
    return f"lip[{shot}:{n}]"


LINE_GAP_S = 0.35


# ---------------------------------------------------------------- hero frames


class FrameShot(BaseModel):
    key: str = Field(pattern=KEY)
    prompt: str = Field(min_length=1, max_length=2000)
    direction: Direction = None


class CinemaFramesInput(BaseModel):
    project_id: uuid.UUID
    shots: list[FrameShot] = Field(min_length=1, max_length=40)
    image_model_id: str = "nano-banana-2"
    aspect_ratio: str = "16:9"
    count: int = Field(4, ge=1, le=9, description="Frames per shot: 4 makes a 2 x 2 grid, 9 a 3 x 3 one")
    which: Literal["start", "end"] = Field("start", description="Opening frames, or closing frames a take ends on")


FRAME_WORDS = {"start": "opening", "end": "closing"}


def frame_prompt(action: str, which: str = "start") -> str:
    return (f"A film still, the {FRAME_WORDS[which]} frame of the shot: {action.strip().rstrip('.')}. "
            "Cinematic composition.")


def frame_kind(prompt: str) -> Literal["start", "end"] | None:
    """Whether a hero frame was made as an opening or a closing frame (its prompt says so: `frame_prompt`)."""
    for which, word in FRAME_WORDS.items():
        if prompt.startswith(f"A film still, the {word} frame of the shot:"):
            return which  # type: ignore[return-value]
    return None


class CinemaFramesRecipe(Recipe):
    id = "cinema_frames"
    name = "Hero frames"
    summary = "Opening frames for each shot, to pick from before filming."
    Input = CinemaFramesInput

    def title(self, inp: CinemaFramesInput) -> str:
        return f"Hero frames: {len(inp.shots)} shot{'s' if len(inp.shots) > 1 else ''}"

    def validate(self, inp: CinemaFramesInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.image_model_id)
        if spec is None or spec.output_type != "image" or text_mode(spec) is None:
            return [f"{inp.image_model_id!r} can't make images from text"]
        return []

    def _mode(self, spec: ModelSpec, prompt: str) -> Mode:
        """References for @mentioned characters when the model takes them; plain text to image otherwise."""
        if mentions(prompt) and (spec.elements or {}).get("strategy") == "refs_native" and (m := ref_mode(spec)):
            return m
        m = text_mode(spec)
        assert m is not None
        return m

    def _batches(self, inp: CinemaFramesInput, spec: ModelSpec, mode: Mode) -> list[int]:
        """How many frames each call makes: as many as the model makes at once (`num_images`), else one each."""
        most = next((getattr(p, "max", 1) for p in spec.params_for(mode.id) if p.name == "num_images"), 1)
        full, rest = divmod(inp.count, max(1, int(most)))
        return [int(most)] * full + ([rest] if rest else [])

    def _params(self, inp: CinemaFramesInput, spec: ModelSpec, mode: Mode, n: int) -> dict[str, Any]:
        params = with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)
        return with_param(spec, mode.id, params, "num_images", n) if n > 1 else params

    def _keys(self, inp: CinemaFramesInput, shot: FrameShot, batches: list[int]) -> list[str]:
        """Step keys: one call per shot keeps the single key earlier runs used; closing frames are their own steps."""
        prefix = "frames" if inp.which == "start" else "ends"
        if len(batches) == 1:
            return [f"{prefix}[{shot.key}]"]
        return [f"{prefix}[{shot.key}:{n}]" for n in range(1, len(batches) + 1)]

    def jobs(self, inp: CinemaFramesInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.image_model_id]
        role = (spec.elements or {}).get("ref_role", "image_references")
        out = []
        for shot in inp.shots:
            mode = self._mode(spec, shot.prompt)
            # the @mentioned characters' pictures fill the reference slot when the frame is made
            roles = (role,) if mode.slot(role) else ()
            out += [PlannedJob(spec.id, mode.id, self._params(inp, spec, mode, n), roles, key=shot.key)
                    for n in self._batches(inp, spec, mode)]
        return out

    def plan(self, inp: CinemaFramesInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.image_model_id]
        specs = []
        word = FRAME_WORDS[inp.which]
        for shot in inp.shots:
            mode = self._mode(spec, shot.prompt)
            batches = self._batches(inp, spec, mode)
            for key, n in zip(self._keys(inp, shot, batches), batches, strict=True):
                specs.append(StepSpec(key, StepKind.generate, f"{word.capitalize()} frames: {shot.prompt[:60]}", {
                    "model_id": spec.id, "mode": mode.id, "prompt": frame_prompt(shot.prompt, inp.which),
                    "media": [], "params": self._params(inp, spec, mode, n), "direction": shot.direction,
                    "project_id": str(inp.project_id), "shot_key": shot.key}, optional=True))
        return specs

    def result(self, inp: CinemaFramesInput, settled: Settled) -> dict[str, Any] | None:
        spec = get_registry().models[inp.image_model_id]
        out: dict[str, list[str]] = {}
        for shot in inp.shots:
            keys = self._keys(inp, shot, self._batches(inp, spec, self._mode(spec, shot.prompt)))
            if not all(k in settled for k in keys):
                return None
            out[shot.key] = [a for k in keys for a in (settled[k].get("asset_ids") or [])]
        return {"frames": out, "which": inp.which}


# ---------------------------------------------------------------- takes


def _lines(shot: TakeShot | SceneShot) -> list[DialogueLine]:
    """The shot's dialogue, which travels in its direction (checked and without blank lines: `checked_direction`)."""
    return [DialogueLine.model_validate(x) for x in (shot.direction or {}).get("dialogue") or []]


def _voice_params(line: DialogueLine) -> dict[str, Any]:
    return {"voice_id": line.voice_id} if line.voice_id else {}


class TakeShot(BaseModel):
    key: str = Field(pattern=KEY)
    prompt: str = Field(min_length=1, max_length=2000)
    direction: Direction = None
    duration: int | None = Field(None, ge=1, le=30)
    start_asset_id: uuid.UUID | None = Field(None, description="The picked hero frame")
    continue_from: str | None = Field(None, pattern=KEY, description="An earlier shot in this run to continue from")
    continue_from_asset_id: uuid.UUID | None = Field(None, description="A picked take to continue from")
    end_asset_id: uuid.UUID | None = Field(None, description="The picked closing frame: the take ends on it")

    def starts_on_a_frame(self) -> bool:
        return bool(self.start_asset_id or self.continue_from or self.continue_from_asset_id)

    def roles(self) -> set[str]:
        """The frames the take is given: where it starts, and where it ends when a closing frame is picked."""
        return ({"start_image"} if self.starts_on_a_frame() else set()) | ({"end_image"} if self.end_asset_id else set())


def take_mode(spec: ModelSpec, shot: TakeShot) -> Mode | None:
    """Start and end frames pick the model's mode that takes both; a start frame alone, image to video."""
    return bridge_mode(spec) if shot.end_asset_id else mode_for(spec, shot.roles())


class CinemaTakesInput(BaseModel):
    project_id: uuid.UUID
    model_id: str
    params: dict[str, Any] = {}
    takes: int = Field(2, ge=1, le=4)
    shots: list[TakeShot] = Field(min_length=1, max_length=40)
    voice_model: str = Field("eleven-v4", description="Voices dialogue on models that don't speak it themselves")
    lipsync_model_id: str = Field("sync-lipsync-3", description="Lip-syncs those takes to their dialogue")

    @model_validator(mode="after")
    def _chains_point_back(self) -> CinemaTakesInput:
        seen: set[str] = set()
        for shot in self.shots:
            if shot.continue_from and shot.continue_from not in seen:
                raise ValueError(f"shot {shot.key} continues from {shot.continue_from}, which must come before it")
            seen.add(shot.key)
        return self


class CinemaTakesRecipe(Recipe):
    id = "cinema_takes"
    name = "Film the shots"
    summary = "Several takes of each shot, chained where a shot continues the one before."
    Input = CinemaTakesInput

    def title(self, inp: CinemaTakesInput) -> str:
        return f"Takes: {len(inp.shots)} shot{'s' if len(inp.shots) > 1 else ''} x {inp.takes}"

    def validate(self, inp: CinemaTakesInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        if spec is None or spec.output_type != "video":
            return [f"{inp.model_id!r} is not a video model"]
        needs_frame = any(s.starts_on_a_frame() for s in inp.shots)
        if needs_frame and start_frame_mode(spec) is None:
            return [f"{spec.name} can't start from an image, so hero frames and continuity can't be used"]
        if not all(s.starts_on_a_frame() for s in inp.shots) and text_mode(spec) is None:
            return [f"{spec.name} needs a hero frame for every shot"]
        ending = [s for s in inp.shots if s.end_asset_id]
        if any(not s.starts_on_a_frame() for s in ending):
            return ["a shot with a closing frame needs an opening frame too (pick one, or continue the shot before)"]
        if ending and bridge_mode(spec) is None:
            return [f"{spec.name} can't end on a picked frame; pick a model with a start-and-end-frame mode"]
        if any(self._voiced_after(inp, spec, s, reg) for s in inp.shots):
            voice, lip = reg.model(inp.voice_model), reg.model(inp.lipsync_model_id)
            if voice is None or mode_with_op(voice, "tts") is None:
                return [f"{spec.name} doesn't voice dialogue, and {inp.voice_model!r} can't speak the lines"]
            if lip is None or lipsync_mode(lip, "video") is None:
                return [f"{spec.name} doesn't voice dialogue, and {inp.lipsync_model_id!r} can't lip-sync a take"]
        return []

    def _params(self, inp: CinemaTakesInput, spec: ModelSpec, mode: Mode, shot: TakeShot,
                reg: Registry | None = None) -> dict[str, Any]:
        params = with_param(spec, mode.id, dict(inp.params), "duration", shot.duration)
        if reg is not None and _lines(shot) and self._speaks(spec, mode, params, reg):
            params = with_param(spec, mode.id, params, "generate_audio", True)  # dialogue needs the sound on
        return params

    @staticmethod
    def _speaks(spec: ModelSpec, mode: Mode, params: dict[str, Any], reg: Registry) -> bool:
        """Whether the model voices dialogue written in its prompt (a virtual model: its engine)."""
        try:
            return resolve_virtual(reg, spec, mode.id, params, None).spec.prompt.dialogue
        except RequestError:
            return False

    def _voiced_after(self, inp: CinemaTakesInput, spec: ModelSpec, shot: TakeShot, reg: Registry) -> bool:
        """Lines this model can't speak: voiced and lip-synced onto each take afterwards."""
        if not _lines(shot):
            return False
        mode = take_mode(spec, shot)
        return mode is not None and not self._speaks(spec, mode, dict(inp.params), reg)

    def jobs(self, inp: CinemaTakesInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        out = []
        for shot in inp.shots:
            mode = take_mode(spec, shot)
            assert mode is not None
            out += [PlannedJob(spec.id, mode.id, self._params(inp, spec, mode, shot, reg), tuple(sorted(shot.roles())),
                               key=shot.key)] * inp.takes
            if self._voiced_after(inp, spec, shot, reg):
                lines = _lines(shot)
                voice = reg.models[inp.voice_model]
                tts = mode_with_op(voice, "tts")
                lip = reg.models[inp.lipsync_model_id]
                lip_mode = lipsync_mode(lip, "video")
                assert tts is not None and lip_mode is not None
                out += [PlannedJob(voice.id, tts.id, _voice_params(line), prompt_chars=len(line.text), key=shot.key)
                        for line in lines]
                speech = sum(len(x.text) for x in lines) / CHARS_PER_SECOND + LINE_GAP_S * (len(lines) - 1)
                out += [PlannedJob(lip.id, lip_mode.id, {}, ("input_video", "input_audio"), planned_seconds=(
                    ("input_video", float(shot.duration or 5)), ("input_audio", max(1.0, speech))),
                    key=shot.key)] * inp.takes
        return out

    def plan(self, inp: CinemaTakesInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        specs: list[StepSpec] = []
        for shot in inp.shots:
            start: str | None = str(shot.start_asset_id) if shot.start_asset_id else None
            deps: tuple[str, ...] = ()
            source = str(shot.continue_from_asset_id) if shot.continue_from_asset_id else None
            if shot.continue_from and not source:
                # wait for every take of the shot it continues, then use the first good one (by take number), so
                # the choice never changes as late takes come in
                keys = tuple(take_key(shot.continue_from, n) for n in range(1, inp.takes + 1))
                if not all(k in settled for k in keys):
                    continue
                first = next((ok(settled, k) for k in keys if ok(settled, k)), None)
                if first is None:
                    continue  # nothing to continue from; reported in the result
                source, deps = first["asset_id"], keys
            if source:
                specs.append(StepSpec(chain_key(shot.key), StepKind.local, f"Last frame before {shot.key}",
                                      {"op": "last_frame", "asset_id": source}, deps))
                frame = ok(settled, chain_key(shot.key))
                if frame is None:
                    continue
                start, deps = frame["asset_id"], (chain_key(shot.key),)
            media = [{"role": "start_image", "asset_id": start}] if start else []
            if shot.end_asset_id:
                media.append({"role": "end_image", "asset_id": str(shot.end_asset_id)})
            mode = take_mode(spec, shot)
            assert mode is not None
            for n in range(1, inp.takes + 1):
                specs.append(StepSpec(take_key(shot.key, n), StepKind.generate, f"{shot.key} take {n}", {
                    "model_id": spec.id, "mode": mode.id, "prompt": shot.prompt, "media": media,
                    "params": self._params(inp, spec, mode, shot, reg), "direction": shot.direction,
                    "project_id": str(inp.project_id), "shot_key": shot.key, "take": n,
                }, deps, optional=True))
            if self._voiced_after(inp, spec, shot, reg):
                specs += self._dialogue_steps(inp, shot, settled, reg)
        return specs

    def _dialogue_steps(self, inp: CinemaTakesInput, shot: TakeShot, settled: Settled,
                        reg: Registry) -> list[StepSpec]:
        """Each line voiced, the lines joined into one track, and each take lip-synced to it (a new take)."""
        lines = _lines(shot)
        voice = reg.models[inp.voice_model]
        tts = mode_with_op(voice, "tts")
        assert tts is not None
        pid = str(inp.project_id)
        keys = [line_key(shot.key, k) for k in range(1, len(lines) + 1)]
        specs = [StepSpec(key, StepKind.generate, f"{shot.key} line {k}: {line.text[:40]}", {
            "model_id": voice.id, "mode": tts.id, "prompt": line.text, "params": _voice_params(line),
            "project_id": pid}, optional=True) for k, (key, line) in enumerate(zip(keys, lines, strict=True), 1)]
        voiced = [ok(settled, k) for k in keys]
        if not all(voiced):
            return specs
        specs.append(StepSpec(track_key(shot.key), StepKind.local, f"{shot.key} dialogue", {
            "op": "join_audio", "asset_ids": [v["asset_id"] for v in voiced if v], "gap": LINE_GAP_S,
            "shot_key": shot.key, "title": f"Dialogue: {lines[0].text[:60]}"}, tuple(keys)))
        track = ok(settled, track_key(shot.key))
        if track is None:
            return specs
        lip = reg.models[inp.lipsync_model_id]
        lip_mode = lipsync_mode(lip, "video")
        assert lip_mode is not None
        for n in range(1, inp.takes + 1):
            take = ok(settled, take_key(shot.key, n))
            if take is not None:
                specs.append(StepSpec(lip_key(shot.key, n), StepKind.generate, f"{shot.key} take {n}: lip sync", {
                    "model_id": lip.id, "mode": lip_mode.id, "prompt": "", "params": {},
                    "media": [{"role": "input_video", "asset_id": take["asset_id"]},
                              {"role": "input_audio", "asset_id": track["asset_id"]}],
                    "project_id": pid, "shot_key": shot.key}, (take_key(shot.key, n), track_key(shot.key)),
                    optional=True))
        return specs

    def result(self, inp: CinemaTakesInput, settled: Settled) -> dict[str, Any] | None:
        out: dict[str, list[str]] = {}
        voiced: dict[str, list[str]] = {}
        for shot in inp.shots:
            keys = [take_key(shot.key, n) for n in range(1, inp.takes + 1)]
            if not all(k in settled for k in keys):
                # a chained shot whose source never produced a take can't be filmed; report it as missing
                if shot.continue_from and all(take_key(shot.continue_from, n) in settled and
                                              not ok(settled, take_key(shot.continue_from, n))
                                              for n in range(1, inp.takes + 1)):
                    out[shot.key] = []
                    continue
                return None
            out[shot.key] = [settled[k]["asset_id"] for k in keys if ok(settled, k)]
            # the engine finishes a run only once every planned step is done, dialogue steps included
            lips = [ok(settled, lip_key(shot.key, n)) for n in range(1, inp.takes + 1)]
            if any(lips):
                voiced[shot.key] = [x["asset_id"] for x in lips if x]
        return {"takes": out, **({"lip_synced": voiced} if voiced else {})}

    def failure(self, inp: CinemaTakesInput, settled: Settled) -> str | None:
        result = self.result(inp, settled)
        if result is not None and not any(result["takes"].values()):
            return "No take came out. Check each take's error and try again."
        return None


# ---------------------------------------------------------------- export


class CutClip(BaseModel):
    asset_id: uuid.UUID
    trim_in: float = Field(0.0, ge=0)
    trim_out: float | None = Field(None, gt=0)
    transition: Literal["cut", "fade"] = "cut"
    volume: float = Field(1.0, ge=0, le=2, description="The take's own sound; 0 when its dialogue is on a track")


class CutCard(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    subtext: str = Field("", max_length=200)
    duration: float = Field(3.0, ge=0.5, le=20)
    background: str = Field("#0d0e10", pattern=r"^#[0-9a-fA-F]{6}$")
    color: str = Field("#f2efe8", pattern=r"^#[0-9a-fA-F]{6}$")
    transition: Literal["cut", "fade"] = "fade"


class CutEntry(BaseModel):
    """One item on the picture track: a take or a title card."""

    take: CutClip | None = None
    card: CutCard | None = None

    @model_validator(mode="after")
    def _one(self) -> CutEntry:
        if (self.take is None) == (self.card is None):
            raise ValueError("each item is either a take or a title card")
        return self


class CutSound(BaseModel):
    asset_id: uuid.UUID
    track: Literal["dialogue", "music", "sfx"]
    start: float = Field(0.0, ge=0, le=3600)
    trim_in: float = Field(0.0, ge=0)
    length: float | None = Field(None, gt=0)
    volume: float = Field(1.0, ge=0, le=2)
    fade_in: float = Field(0.0, ge=0, le=10)
    fade_out: float = Field(0.0, ge=0, le=10)


class CinemaExportInput(BaseModel):
    project_id: uuid.UUID
    title: str = Field("Cut", max_length=200)
    items: list[CutEntry] = Field(default_factory=list, max_length=200)
    audio: list[CutSound] = Field(default_factory=list, max_length=100)
    fade_seconds: float = Field(0.5, ge=0.1, le=2.0)
    duck_music: bool = Field(True, description="Lower the music while dialogue plays")

    @model_validator(mode="before")
    @classmethod
    def _clips_as_items(cls, data: Any) -> Any:
        """Exports used to send only `clips` (takes)."""
        if isinstance(data, dict) and data.get("clips") and not data.get("items"):
            data = {**data, "items": [{"take": c} for c in data["clips"]]}
            data.pop("clips")
        return data

    @model_validator(mode="after")
    def _something(self) -> CinemaExportInput:
        if not self.items:
            raise ValueError("the cut needs at least one take or title card")
        return self


class CinemaExportRecipe(Recipe):
    id = "cinema_export"
    name = "Export the cut"
    summary = "The picked takes and title cards in order, trimmed and joined, with dialogue, music and SFX."
    Input = CinemaExportInput

    def title(self, inp: CinemaExportInput) -> str:
        return f"Cut: {inp.title}"

    def jobs(self, inp: CinemaExportInput, reg: Registry) -> list[PlannedJob]:
        return []

    def plan(self, inp: CinemaExportInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        return [StepSpec("cut", StepKind.local, "Cut the film", {
            "op": "timeline_cut", "title": inp.title, "fade_seconds": inp.fade_seconds, "duck_music": inp.duck_music,
            "items": [e.model_dump(mode="json", exclude_none=True) for e in inp.items],
            "audio": [a.model_dump(mode="json") for a in inp.audio]})]

    def result(self, inp: CinemaExportInput, settled: Settled) -> dict[str, Any] | None:
        cut = ok(settled, "cut")
        return ({"asset_id": cut["asset_id"], "duration_ms": cut.get("duration_ms"), "frames": cut.get("frames")}
                if cut else None)


# ---------------------------------------------------------------- a scene in one take (multi-shot)


def scene_take_key(scene: str, n: int) -> str:
    return f"scene[{scene}:{n}]"


def split_key(scene: str, n: int) -> str:
    return f"split[{scene}:{n}]"


class SceneShot(BaseModel):
    key: str = Field(pattern=KEY)
    prompt: str = Field(min_length=1, max_length=2000)
    direction: Direction = None
    duration: float = Field(ge=1, le=30)


class CinemaSceneInput(BaseModel):
    project_id: uuid.UUID
    model_id: str
    params: dict[str, Any] = {}
    takes: int = Field(1, ge=1, le=4)
    scene_key: str = Field(pattern=KEY)
    shots: list[SceneShot] = Field(min_length=2, max_length=8)
    start_asset_id: uuid.UUID | None = Field(None, description="The first shot's picked hero frame")


class CinemaSceneRecipe(Recipe):
    id = "cinema_scene"
    name = "Film a scene in one take"
    summary = "The scene's shots rendered as one multi-shot take, then cut back into a take per shot."
    Input = CinemaSceneInput

    def title(self, inp: CinemaSceneInput) -> str:
        return f"Scene in one take: {len(inp.shots)} shots"

    def _mode(self, inp: CinemaSceneInput, spec: ModelSpec) -> Mode | None:
        return multi_shot_mode(spec, frame=inp.start_asset_id is not None)

    def validate(self, inp: CinemaSceneInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        if spec is None or spec.output_type != "video":
            return [f"{inp.model_id!r} is not a video model"]
        if self._mode(inp, spec) is None:
            frame = " from a hero frame" if inp.start_asset_id else ""
            return [f"{spec.name} can't film several shots in one take{frame}"]
        return []

    def _params(self, inp: CinemaSceneInput, spec: ModelSpec, mode: Mode, reg: Registry) -> dict[str, Any]:
        params = {k: v for k, v in inp.params.items() if k != "duration"}  # the shots set the length
        if any(_lines(s) for s in inp.shots) and CinemaTakesRecipe._speaks(spec, mode, params, reg):
            params = with_param(spec, mode.id, params, "generate_audio", True)  # spoken lines need the sound on
        name = next((p.name for p in spec.params_for(mode.id) if p.type == "shots"), "shots")
        return {**params, name: [{"prompt": s.prompt, "duration": s.duration,
                                  **({"direction": s.direction} if s.direction else {})} for s in inp.shots]}

    def jobs(self, inp: CinemaSceneInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        mode = self._mode(inp, spec)
        assert mode is not None
        roles = ("start_image",) if inp.start_asset_id else ()
        return [PlannedJob(spec.id, mode.id, self._params(inp, spec, mode, reg), roles)] * inp.takes

    def plan(self, inp: CinemaSceneInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        mode = self._mode(inp, spec)
        assert mode is not None
        media = [{"role": "start_image", "asset_id": str(inp.start_asset_id)}] if inp.start_asset_id else []
        specs: list[StepSpec] = []
        for n in range(1, inp.takes + 1):
            key = scene_take_key(inp.scene_key, n)
            # the whole take belongs to the scene, not one shot; its pieces become the shots' takes
            specs.append(StepSpec(key, StepKind.generate, f"Scene take {n}", {
                "model_id": spec.id, "mode": mode.id, "prompt": "", "media": media,
                "params": self._params(inp, spec, mode, reg), "project_id": str(inp.project_id), "take": n,
            }, optional=True))
            take = ok(settled, key)
            if take is not None:
                specs.append(StepSpec(split_key(inp.scene_key, n), StepKind.local, f"Cut scene take {n} into shots", {
                    "op": "split_shots", "asset_id": take["asset_id"], "project_id": str(inp.project_id),
                    "scene_key": inp.scene_key,
                    "shots": [{"key": s.key, "duration": s.duration, "prompt": s.prompt} for s in inp.shots],
                }, (key,)))
        return specs

    def result(self, inp: CinemaSceneInput, settled: Settled) -> dict[str, Any] | None:
        out: dict[str, list[str]] = {s.key: [] for s in inp.shots}
        scenes = []
        for n in range(1, inp.takes + 1):
            key = scene_take_key(inp.scene_key, n)
            if key not in settled:
                return None
            if not ok(settled, key):
                continue
            split = ok(settled, split_key(inp.scene_key, n))
            if split is None:
                return None
            scenes.append(settled[key]["asset_id"])
            for shot_key, asset_id in split["assets"].items():
                out[shot_key].append(asset_id)
        return {"takes": out, "scene_takes": scenes}

    def failure(self, inp: CinemaSceneInput, settled: Settled) -> str | None:
        result = self.result(inp, settled)
        if result is not None and not result["scene_takes"]:
            return "No take of the scene came out. Check each take's error and try again."
        return None


# ---------------------------------------------------------------- extend a take


EDGE = 1 / 24  # the frame two joined clips share, dropped from one of them


class CinemaExtendInput(BaseModel):
    project_id: uuid.UUID
    shot_key: str = Field(pattern=KEY)
    take_asset_id: uuid.UUID
    side: Literal["after", "before"] = Field("after", description="Continue past the end, or lead into the start")
    prompt: str = Field(min_length=1, max_length=2000, description="What happens in the new part")
    direction: dict[str, Any] | None = None
    model_id: str
    params: dict[str, Any] = {}
    duration: int = Field(5, ge=1, le=15)
    image_model_id: str = Field("nano-banana-2", description="Before: draws the frame the new part starts on")


def lead_frame_prompt(prompt: str) -> str:
    return (f"The same scene a few moments earlier: {prompt.strip().rstrip('.')}. Keep the same people, place, "
            "light and style as the reference frame.")


class CinemaExtendRecipe(Recipe):
    id = "cinema_extend"
    name = "Extend a take"
    summary = "More of a shot: continue from its last frame, or lead into its first, joined into a new take."
    Input = CinemaExtendInput

    def title(self, inp: CinemaExtendInput) -> str:
        return f"Extend {inp.shot_key} {'forward' if inp.side == 'after' else 'backward'}: {inp.prompt[:60]}"

    def validate(self, inp: CinemaExtendInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        if spec is None or spec.output_type != "video":
            return [f"{inp.model_id!r} is not a video model"]
        if inp.side == "after" and start_frame_mode(spec) is None:
            return [f"{spec.name} can't start from a frame, so it can't continue a take"]
        if inp.side == "before":
            image = reg.model(inp.image_model_id)
            if bridge_mode(spec) is None:
                return [f"{spec.name} can't end on a chosen frame, so it can't lead into a take; "
                        "pick a model with start and end frames"]
            if image is None or image.output_type != "image" or ref_mode(image) is None:
                return [f"{inp.image_model_id!r} can't draw a frame from a reference picture"]
        return []

    def _video(self, inp: CinemaExtendInput, spec: ModelSpec) -> tuple[Mode, dict[str, Any]]:
        mode = start_frame_mode(spec) if inp.side == "after" else bridge_mode(spec)
        assert mode is not None
        return mode, with_param(spec, mode.id, dict(inp.params), "duration", fit_duration(spec, mode.id, inp.duration))

    def jobs(self, inp: CinemaExtendInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        mode, params = self._video(inp, spec)
        if inp.side == "after":
            return [PlannedJob(spec.id, mode.id, params, ("start_image",))]
        image = reg.models[inp.image_model_id]
        img_mode, role = ref_image(image)
        return [PlannedJob(image.id, img_mode.id, {}, (role,)),
                PlannedJob(spec.id, mode.id, params, ("start_image", "end_image"))]

    def plan(self, inp: CinemaExtendInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        mode, params = self._video(inp, spec)
        take, pid = str(inp.take_asset_id), str(inp.project_id)
        common = {"project_id": pid, "shot_key": inp.shot_key, "prompt": inp.prompt}
        op = "last_frame" if inp.side == "after" else "first_frame"
        label = "Last frame of the take" if inp.side == "after" else "First frame of the take"
        specs = [StepSpec("edge", StepKind.local, label, {"op": op, "asset_id": take})]
        edge = ok(settled, "edge")
        if edge is None:
            return specs
        # the new part is made for the project but isn't a take of the shot by itself; the joined clip is
        video_step = {"model_id": spec.id, "mode": mode.id, "prompt": inp.prompt, "params": params,
                      "direction": inp.direction, "project_id": pid}
        if inp.side == "after":
            specs.append(StepSpec("more", StepKind.generate, "What happens next",
                                  {**video_step, "media": [{"role": "start_image", "asset_id": edge["asset_id"]}]},
                                  ("edge",)))
            more = ok(settled, "more")
            if more is not None:
                specs.append(StepSpec("join", StepKind.local, "Join into a longer take", {
                    "op": "join_takes", **common, "made_by": "extend_after", "parent_asset_id": take,
                    "clips": [{"asset_id": take}, {"asset_id": more["asset_id"], "trim_in": EDGE}]}, ("more",)))
            return specs

        image = reg.models[inp.image_model_id]
        img_mode, role = ref_image(image)
        specs.append(StepSpec("lead_frame", StepKind.generate, "The frame it starts on", {
            "model_id": image.id, "mode": img_mode.id, "prompt": lead_frame_prompt(inp.prompt),
            "media": [{"role": role, "asset_id": edge["asset_id"]}],
            "params": with_param(image, img_mode.id, {}, "aspect_ratio", inp.params.get("aspect_ratio")),
            "project_id": pid}, ("edge",)))
        start = ok(settled, "lead_frame")
        if start is None:
            return specs
        first = (start.get("asset_ids") or [start["asset_id"]])[0]
        specs.append(StepSpec("lead", StepKind.generate, "What happens just before", {
            **video_step, "media": [{"role": "start_image", "asset_id": first},
                                    {"role": "end_image", "asset_id": edge["asset_id"]}]}, ("lead_frame",)))
        lead = ok(settled, "lead")
        if lead is not None:
            seconds = (lead.get("duration_ms") or inp.duration * 1000) / 1000
            specs.append(StepSpec("join", StepKind.local, "Join into a longer take", {
                "op": "join_takes", **common, "made_by": "extend_before", "parent_asset_id": take,
                "clips": [{"asset_id": lead["asset_id"], "trim_out": max(EDGE, seconds - EDGE)}, {"asset_id": take}],
            }, ("lead",)))
        return specs

    def result(self, inp: CinemaExtendInput, settled: Settled) -> dict[str, Any] | None:
        join = ok(settled, "join")
        return {"asset_id": join["asset_id"], "duration_ms": join.get("duration_ms")} if join else None


# ---------------------------------------------------------------- edit a take


class CinemaEditInput(BaseModel):
    project_id: uuid.UUID
    shot_key: str = Field(pattern=KEY)
    take_asset_id: uuid.UUID
    model_id: str
    prompt: str = Field(min_length=1, max_length=2000, description="What to change")
    params: dict[str, Any] = {}
    reference_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)


class CinemaEditRecipe(Recipe):
    id = "cinema_edit"
    name = "Edit a take"
    summary = "Change a whole take by instruction with an Edit Video model; the result is a new take of the shot."
    Input = CinemaEditInput

    def title(self, inp: CinemaEditInput) -> str:
        return f"Edit {inp.shot_key}: {inp.prompt[:60]}"

    def validate(self, inp: CinemaEditInput, reg: Registry) -> list[str]:
        spec = reg.model(inp.model_id)
        mode = edit_mode(spec) if spec else None
        if spec is None or mode is None:
            return [f"{inp.model_id!r} can't edit a clip by instruction"]
        if inp.reference_asset_ids and mode.slot("image_references") is None:
            return [f"{spec.name} doesn't take reference pictures for an edit"]
        return []

    def jobs(self, inp: CinemaEditInput, reg: Registry) -> list[PlannedJob]:
        spec = reg.models[inp.model_id]
        mode = edit_mode(spec)
        assert mode is not None
        roles = ("input_video", *(["image_references"] if inp.reference_asset_ids else []))
        return [PlannedJob(spec.id, mode.id, dict(inp.params), roles,
                           media=(("input_video", str(inp.take_asset_id)),))]

    def plan(self, inp: CinemaEditInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        spec = reg.models[inp.model_id]
        mode = edit_mode(spec)
        assert mode is not None
        media = [{"role": "input_video", "asset_id": str(inp.take_asset_id)},
                 *({"role": "image_references", "asset_id": str(a)} for a in inp.reference_asset_ids)]
        # an ordinary take of the shot; its input video is its parent
        return [StepSpec("edit", StepKind.generate, f"Edit: {inp.prompt[:60]}", {
            "model_id": spec.id, "mode": mode.id, "prompt": inp.prompt, "media": media, "params": dict(inp.params),
            "project_id": str(inp.project_id), "shot_key": inp.shot_key})]

    def result(self, inp: CinemaEditInput, settled: Settled) -> dict[str, Any] | None:
        edit = ok(settled, "edit")
        return {"asset_id": edit["asset_id"]} if edit else None
