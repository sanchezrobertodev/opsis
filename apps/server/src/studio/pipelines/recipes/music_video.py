"""Music video: your song becomes a cut of shots that follow its lyrics, optionally starring someone from a picture.

  transcribe (speech-to-text: every sung word with its time) -> shots (cut the song at the pauses in its lyrics)
  -> plan (Claude: one shot per section from its lyrics, and the video's world)
  -> per shot: frame[sNN] (their picture as the reference, or from text) -> clip[sNN] (animated, as long as its section)
  -> parts (the song cut at the shots) -> cut (shots back to back over the song) -> captions (the lyrics, word-timed)

A clip that fails falls back to its picture, so one refused shot never sinks the video. A song with no words heard
(an instrumental) still gets its shots, from the idea alone, and no captions.
"""

from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.db.models import StepKind
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import (
    LINES_PER_SECTION,
    MAX_SECTIONS,
    MAX_SONG_S,
    Song,
    fit_song,
    section_seconds,
    song_lines,
    song_text,
)
from studio.faceless.writer import _mock_song as mock_song
from studio.llm.client import LLMError, LLMResult, structured
from studio.pipelines.recipe import PlannedJob, Recipe, Settled, StepSpec, ok
from studio.pipelines.recipes.common import (
    clip_params,
    mode_with_op,
    start_frame_mode,
    text_mode,
    with_param,
)
from studio.pipelines.recipes.vlog import SINGLE_FRAME, SLUG, frame_mode
from studio.registry.loader import Registry
from studio.registry.schema import Mode, ModelSpec

TARGET_S, MIN_S = 6.0, 3.0  # a shot's length: about six seconds, at least three
MAX_SHOT_S = 10.0  # the longest clip we ask for, whatever the video model allows
SONG_GUESS_S = 180  # the estimate before the song is measured: a three-minute song


class MusicVideoInput(BaseModel):
    song: uuid.UUID | None = Field(None, description="The song (an audio file or a clip with sound)")
    brief: str = Field("", max_length=500, description="Or what a new song should be about: Claude writes the "
                                                       "lyrics and a music model sings them")
    style: str = Field("", max_length=200, description="How the new song sounds: genre, instruments, voice")
    minutes: float = Field(1.5, ge=0.5, le=4, description="About how long the new song runs")
    language: str = Field("English", max_length=40)
    music_model: str = "eleven-music"
    review_lyrics: bool = Field(True, description="Stop for approval before the new song is sung")
    idea: str = Field("", max_length=500, description="What the video is about or how it looks")
    character_image: uuid.UUID | None = Field(None, description="Someone to star in every shot")
    character: str | None = Field(None, pattern=SLUG, description="Or a saved character's @handle to star")
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: Literal["720p", "1080p"] = "1080p"
    max_shots: int = Field(24, ge=4, le=40)
    transcribe_model: str = "scribe-v2"
    image_model: str = "nano-banana-2"
    video_model: str = "ltx-2.5"
    captions: bool = True
    caption_style: str = "editorial-motion-graphics"

    @model_validator(mode="after")
    def _one_star(self) -> MusicVideoInput:
        if self.character_image is not None and self.character is not None:
            raise ValueError("give a picture of the star or pick a saved character, not both")
        if (self.song is None) == (not self.brief.strip()):
            raise ValueError("give a song or a brief for a new one, not both")
        return self

    @property
    def star(self) -> bool:
        return self.character_image is not None or self.character is not None


# ---------------------------------------------------------------- a new song (Claude writes, a music model sings)


LYRICS_SYSTEM = """You write songs for music videos: the lyrics are sung by a vocal music model, then the video is cut
to them.

Write verses and a repeated chorus (repeat the chorus section in full each time it comes back), in at most 12 sections.
Lines are short, rhythmic and singable, with natural rhymes, in the voice and mood the brief asks for. Hit the target
length closely: each line takes about four seconds to sing.

Give `music` as a few comma-separated styles (genre, instruments, voice, tempo) that fit the brief. Give each section a
`picture` (one concrete moment it suggests, no art-style words) and `world` one sentence on the look; the video's
director uses them as hints."""


async def write_lyrics(session: AsyncSession, user_id: uuid.UUID, *, brief: str, style: str, minutes: float,
                       language: str) -> LLMResult[Song]:
    prompt = "\n".join(line for line in [
        f"Brief: {brief}",
        f"How it should sound: {style}" if style else "",
        f"Length: about {song_lines(minutes)} sung lines in all, choruses included.",
        f"Language: {language}.",
    ] if line)
    result = await structured(
        session, user_id, purpose="music_video.lyrics", model=get_settings().llm_smart_model, system=LYRICS_SYSTEM,
        prompt=prompt, output=Song, mock=lambda: mock_song(brief), effort="medium", max_tokens=8000)
    song = result.output
    song.sections = [x for x in song.sections if any(line.strip() for line in x.lines)][:MAX_SECTIONS]
    if not song.sections:
        raise LLMError("Claude wrote a song without lyrics. Try again.", retryable=True)
    return result


# ---------------------------------------------------------------- the shot plan (Claude)


class MusicShot(BaseModel):
    visual: str = Field(description="One concrete shot: subject, action, setting, framing. No art-style words")
    motion: str = Field(description="How the shot moves: camera and subject motion")


class MusicPlan(BaseModel):
    world: str = Field(description="One sentence fixing the video's look: place, era, palette, light")
    shots: list[MusicShot]


PLAN_SYSTEM = """You direct music videos. You get the song's sections in order, each with the lyrics sung over it
(or nothing for an instrumental part), and plan one shot per section.

- Follow the lyrics: show what they're about, literally or as a clear visual metaphor; instrumental parts carry the
  mood. Build a loose story across the video and vary the framing (wide, medium, close-up, overhead).
- `visual` is concrete: subject, action, setting, framing. Never mention an art style or rendering. No text, letters
  or logos in the picture.
- `visual` is one moment, the shot's opening frame: a single picture, never two actions or "then". What happens next
  (climbing, then lighting the lamp) goes in `motion`.
- When a star is given, they appear in most shots; write "the star" for them and never describe their face.
- When the song's brief is given, keep the people, their relationships and the story it describes: friends stay
  friends, a solo singer stays alone.
- Return exactly one shot per section, in order. `world` pins the look of the whole video in one sentence."""


async def plan_shots(session: AsyncSession, user_id: uuid.UUID, *, sections: list[str], idea: str,
                     star: bool, brief: str = "") -> LLMResult[MusicPlan]:
    listed = "\n".join(f"Section {i}: {text or '(instrumental)'}" for i, text in enumerate(sections, 1))
    prompt = "\n".join(line for line in [
        f"The song's brief (what it's about): {brief}" if brief else "",
        f"Idea: {idea}" if idea else "",
        "A star appears: the person from a reference photo." if star else "No star: show the world of the song.",
        f"\n{listed}",
    ] if line)
    result = await structured(
        session, user_id, purpose="music_video.plan", model=get_settings().llm_smart_model, system=PLAN_SYSTEM,
        prompt=prompt, output=MusicPlan, mock=lambda: _mock_plan(sections, star), effort="medium", max_tokens=8000)
    if len(result.output.shots) != len(sections):
        raise LLMError(f"Claude planned {len(result.output.shots)} shots for {len(sections)} sections. Try again.",
                       retryable=True)
    return result


def _mock_plan(sections: list[str], star: bool) -> MusicPlan:
    framings = ["Wide shot", "Medium shot", "Close-up", "Overhead view"]
    who = "the star" if star else "a city street"
    return MusicPlan(world="A rain-washed city at night, neon reflections, deep blues and warm pinks.", shots=[
        MusicShot(visual=f"{framings[i % 4]} of {who}: {(text or 'the beat drops')[:60]}", motion="slow push in")
        for i, text in enumerate(sections)])


# ---------------------------------------------------------------- the recipe


def frame_key(i: int) -> str:
    return f"frame[s{i:02d}]"


def clip_key(i: int) -> str:
    return f"clip[s{i:02d}]"


def _picture_mode(spec: ModelSpec, star: bool) -> Mode | None:
    return frame_mode(spec) if star else (text_mode(spec) if spec.output_type == "image" else None)


class MusicVideoRecipe(Recipe):
    id = "music_video"
    name = "Music video"
    summary = "Your song as a music video that follows its lyrics, optionally starring someone from a picture."
    Input = MusicVideoInput

    def title(self, inp: MusicVideoInput) -> str:
        return f"Music video: {(inp.idea or inp.brief).strip()[:80] or 'your song'}"

    def validate(self, inp: MusicVideoInput, reg: Registry) -> list[str]:
        errors = []
        if inp.captions and get_catalog().style(inp.caption_style) is None:
            errors.append(f"unknown caption style {inp.caption_style!r}")
        star = inp.star
        checks = [
            (inp.transcribe_model, lambda s: mode_with_op(s, "transcribe"), "hear the lyrics"),
            (inp.image_model, lambda s: _picture_mode(s, star),
             "draw a picture from a reference photo" if star else "draw a picture from text"),
            (inp.video_model, start_frame_mode, "animate a picture"),
        ]
        if inp.song is None:
            checks.append((inp.music_model, lambda s: mode_with_op(s, "song"), "sing a song from its lyrics"))
            if inp.video_model in reg.models and inp.minutes * 60 > (cover := inp.max_shots * self._max_s(inp, reg)):
                errors.append(f"{inp.max_shots} shots cover at most {cover:.0f} s; raise the shot limit or ask for a "
                              f"shorter song than {inp.minutes:g} minutes")
        for model_id, find, what in checks:
            spec = reg.model(model_id)
            if spec is None:
                errors.append(f"unknown model {model_id!r}")
            elif find(spec) is None:
                errors.append(f"{spec.name} can't {what}")
        return errors

    def _sing_params(self, inp: MusicVideoInput, reg: Registry, sections: list[list[str]],
                     names: list[str] | None = None) -> dict[str, Any]:
        """The sung sections, each as long as its lines need, fitted within the longest song the model makes."""
        limit = getattr(reg.models[inp.music_model].param("duration"), "max", None) or MAX_SONG_S
        limit = min(float(limit), inp.max_shots * self._max_s(inp, reg))  # never longer than the shots can cover
        seconds = fit_song([section_seconds(lines) for lines in sections], float(limit))
        return {"sections": [{"name": names[i] if names else f"Part {i + 1}", "lines": lines, "duration": d}
                             for i, (lines, d) in enumerate(zip(sections, seconds, strict=True))]}

    def _song(self, inp: MusicVideoInput, settled: Settled, reg: Registry) -> tuple[list[StepSpec], str | None]:
        """The steps that make a new song from the brief, and the song's asset once it's sung (or the upload)."""
        if inp.song is not None:
            return [], str(inp.song)
        specs = [StepSpec("lyrics", StepKind.llm, "Write the song", {
            "op": "music_video_lyrics", "brief": inp.brief, "style": inp.style, "minutes": inp.minutes,
            "language": inp.language})]
        written = ok(settled, "lyrics")
        if written is None:
            return specs, None
        song = Song.model_validate(written["song"])
        after: tuple[str, ...] = ("lyrics",)
        if inp.review_lyrics:
            specs.append(StepSpec("review", StepKind.checkpoint, "Approve the lyrics", {
                "type": "approve", "options": [{"value": "approve", "label": "Sing these lyrics"}],
                "script": song_text(song), "title": song.title}, after))
            if ok(settled, "review") is None:
                return specs, None
            after = ("review",)
        music = reg.models[inp.music_model]
        specs.append(StepSpec("sing", StepKind.generate, "Sing the song", {
            "model_id": music.id, "mode": mode_with_op(music, "song").id,  # type: ignore[union-attr]
            "prompt": ", ".join(x for x in (inp.style, song.music) if x),
            "params": self._sing_params(inp, reg, [x.lines for x in song.sections], [x.name for x in song.sections]),
            "media": []}, after))
        sung = ok(settled, "sing")
        return specs, sung["asset_id"] if sung else None

    def _ref_role(self, reg: Registry, inp: MusicVideoInput) -> str:
        return (reg.models[inp.image_model].elements or {}).get("ref_role", "image_references")

    def _max_s(self, inp: MusicVideoInput, reg: Registry) -> float:
        p = reg.models[inp.video_model].param("duration")
        return min(MAX_SHOT_S, float(getattr(p, "max", None) or MAX_SHOT_S))

    def _frame_params(self, inp: MusicVideoInput, reg: Registry) -> dict[str, Any]:
        spec = reg.models[inp.image_model]
        mode = _picture_mode(spec, inp.star)
        assert mode is not None
        return with_param(spec, mode.id, {}, "aspect_ratio", inp.aspect_ratio)

    def jobs(self, inp: MusicVideoInput, reg: Registry) -> list[PlannedJob]:
        """A song's length is only known once it's measured, so this prices a three-minute upload (the transcript itself
        at its real length), or a new song of about `minutes`."""
        heard, image, video = (reg.models[m] for m in (inp.transcribe_model, inp.image_model, inp.video_model))
        length = SONG_GUESS_S if inp.song else inp.minutes * 60
        target = max(TARGET_S, length / inp.max_shots)
        n = min(inp.max_shots, math.ceil(length / target))
        pic = _picture_mode(image, inp.star)
        assert pic is not None
        refs = ((self._ref_role(reg, inp), str(inp.character_image)),) if inp.character_image else ()
        out = []
        if inp.song is None:  # before the lyrics exist: about four lines a section
            lines = song_lines(inp.minutes)
            k = min(MAX_SECTIONS, max(1, math.ceil(lines / LINES_PER_SECTION)))
            sections = [["la"] * (lines // k + (i < lines % k)) for i in range(k)]
            out.append(PlannedJob(inp.music_model, mode_with_op(reg.models[inp.music_model], "song").id,  # type: ignore[union-attr]
                                  self._sing_params(inp, reg, sections)))
            out.append(PlannedJob(heard.id, mode_with_op(heard, "transcribe").id, {}, ("input_audio",),  # type: ignore[union-attr]
                                  planned_seconds=(("input_audio", length),)))
        else:
            out.append(PlannedJob(heard.id, mode_with_op(heard, "transcribe").id, {}, ("input_audio",),  # type: ignore[union-attr]
                                  media=(("input_audio", str(inp.song)),)))
        roles = (self._ref_role(reg, inp),) if inp.star else ()  # a saved character's pictures come with the mention
        out += [PlannedJob(image.id, pic.id, self._frame_params(inp, reg), roles, media=refs)
                for _ in range(n)]
        out += [PlannedJob(video.id, start_frame_mode(video).id, clip_params(reg.models[inp.video_model], inp.aspect_ratio, target),  # type: ignore[union-attr]
                           ("start_image",)) for _ in range(n)]
        return out

    def plan(self, inp: MusicVideoInput, settled: Settled, reg: Registry) -> list[StepSpec]:
        heard = reg.models[inp.transcribe_model]
        specs, song = self._song(inp, settled, reg)
        if song is None:
            return specs
        specs.append(StepSpec("transcribe", StepKind.generate, "Hear the lyrics", {
            "model_id": heard.id, "mode": mode_with_op(heard, "transcribe").id, "prompt": "",  # type: ignore[union-attr]
            "params": {}, "media": [{"role": "input_audio", "asset_id": song}]}, ("sing",) if inp.song is None else ()))
        script = ok(settled, "transcribe")
        if script is None:
            return specs
        specs.append(StepSpec("shots", StepKind.local, "Cut the song into shots", {
            "op": "lyric_shots", "audio": song, "transcript": script["asset_id"], "max_shots": inp.max_shots,
            "target_s": TARGET_S, "min_s": MIN_S, "max_s": self._max_s(inp, reg)}, ("transcribe",)))
        cut = ok(settled, "shots")
        if cut is None:
            return specs
        sections: list[dict[str, Any]] = cut["shots"]
        specs.append(StepSpec("plan", StepKind.llm, "Plan the shots", {
            "op": "music_video_plan", "sections": [s["text"] for s in sections], "idea": inp.idea, "brief": inp.brief,
            "star": inp.star, "character": inp.character}, ("shots",)))
        specs.append(StepSpec("parts", StepKind.local, "Cut the song at the shots", {
            "op": "split_audio", "audio": song, "seconds": cut["seconds"], "title": "Music video"},
            ("shots",)))
        plan = ok(settled, "plan")
        if plan is None:
            return specs

        image, video = reg.models[inp.image_model], reg.models[inp.video_model]
        pic, i2v = _picture_mode(image, inp.star), start_frame_mode(video)
        assert pic and i2v
        media = ([{"role": self._ref_role(reg, inp), "asset_id": str(inp.character_image)}]
                 if inp.character_image else [])
        star = (f" The star is @{inp.character}." if inp.character else  # their pictures attach through the mention
                " The star is the person from the reference photo, same face, hair and build." if media else "")
        for i, (shot, section) in enumerate(zip(plan["shots"], sections, strict=True), 1):
            specs.append(StepSpec(frame_key(i), StepKind.generate, f"Shot {i}: picture", {
                "model_id": image.id, "mode": pic.id, "params": self._frame_params(inp, reg), "media": media,
                "prompt": f"{shot['visual'].strip().rstrip('.')}. {plan['world'].strip().rstrip('.')}.{star} "
                          f"{SINGLE_FRAME} Cinematic music video still, no text or logos."}, ("plan",)))
            frame = ok(settled, frame_key(i))
            if frame:  # a failed clip falls back to its picture
                specs.append(StepSpec(clip_key(i), StepKind.generate, f"Shot {i}: motion", {
                    "model_id": video.id, "mode": i2v.id,
                    "prompt": f"{shot['visual'].strip().rstrip('.')}. {shot['motion']}",
                    "params": clip_params(reg.models[inp.video_model], inp.aspect_ratio, section["end"] - section["start"]),
                    "media": [{"role": "start_image", "asset_id": frame["asset_id"]}]}, (frame_key(i),), optional=True))

        parts = (ok(settled, "parts") or {}).get("asset_ids")
        visuals = [self._visual(settled, i) for i in range(1, len(sections) + 1)]
        if not parts or not all(visuals):
            return specs
        specs.append(StepSpec("cut", StepKind.local, "Assemble the video", {
            "op": "narrated_cut", "title": "Music video", "aspect_ratio": inp.aspect_ratio,
            "resolution": inp.resolution, "music": None, "gap_s": 0, "tail_s": 0,
            "blocks": [{"visual": v, "voice": p} for v, p in zip(visuals, parts, strict=True)]},
            ("parts", *(frame_key(i) for i in range(1, len(sections) + 1)),
             *(clip_key(i) for i in range(1, len(sections) + 1)))))
        done = ok(settled, "cut")
        if inp.captions and cut["lyrics"] and done:
            specs.append(StepSpec("captions", StepKind.local, "Burn in the lyrics", {
                "op": "captions", "video": done["asset_id"], "voices": parts, "starts": done["starts"],
                "words": [s["words"] for s in sections], "style": inp.caption_style, "title": "Music video"},
                ("cut",)))
        return specs

    def _visual(self, settled: Settled, i: int) -> str | None:
        """Shot i's clip once it has settled, else its picture."""
        frame = ok(settled, frame_key(i))
        if frame is None or clip_key(i) not in settled:
            return None
        clip = ok(settled, clip_key(i))
        return clip["asset_id"] if clip else frame["asset_id"]

    def result(self, inp: MusicVideoInput, settled: Settled) -> dict[str, Any] | None:
        cut, shots = ok(settled, "cut"), ok(settled, "shots")
        if cut is None or shots is None:
            return None
        if inp.captions and shots["lyrics"]:
            final = ok(settled, "captions")
            if final is None:
                return None
            return {"asset_id": final["asset_id"], "master_asset_id": cut["asset_id"],
                    "captions_asset_id": final.get("subtitle_asset_id")}
        return {"asset_id": cut["asset_id"], "master_asset_id": cut["asset_id"], "captions_asset_id": None}
