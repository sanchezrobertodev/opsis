"""Prompt enhancement: turn a rough idea into a prompt written for one model's dialect.

The director still appends camera moves and the cinema look afterwards, so the enhancer is told what the
shot direction already covers and leaves those out. @mentions are kept verbatim; they are resolved to
saved characters later. Results are cached in-process, so pressing Enhance twice doesn't pay twice.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import OrderedDict

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import get_settings
from studio.director.compiler import ShotDirection
from studio.director.elements import mentions
from studio.llm.client import LLMResult, structured
from studio.registry.schema import ModelSpec

# How each prompt dialect likes to be written. Keys match `prompt.dialect` in registry/models/*.yaml.
DIALECT_STYLE = {
    "veo": "Flowing, concrete sentences in present tense: subject and action first, then setting, light and "
           "texture. Describe sound (dialogue in quotes, ambience, effects) when the model makes audio.",
    "kling": "Compact comma-separated phrases: subject, action beats in order, setting, mood. Physical, "
             "visible detail over adjectives.",
    "wan": "Compact comma-separated phrases: subject, action, setting, light. Short and concrete; this "
           "model loses track of long prompts.",
    "seedance": "Clear sentences that describe one continuous shot: who, what they do in order, where, and "
                "how it looks. Plain language, no keyword stuffing.",
    "runway": "One or two plain sentences describing the visible action and setting. No camera or style "
              "jargon unless it matters to the shot.",
    "nano-banana": "A still image: describe subject, composition, setting, light and material detail. No "
                   "motion or sound.",
    "flux": "A still image: subject first, then composition, setting, light, texture. No motion or sound.",
}
DEFAULT_STYLE = "Clear, concrete description of what is visible: subject, action, setting, light."

SYSTEM = """You write prompts for AI video and image generation models in Opsis, a self-hosted video studio.

You receive a rough idea from the user plus facts about the target model. Rewrite the idea into one prompt
that the model will render well:
- Keep the user's subject, action, setting and intent. Add specific, visible detail that serves it:
  appearance, the order of action beats, materials, weather, light quality, small environmental motion.
- Never invent new main characters or change what happens.
- Copy every @mention (like @maya or @old-town) exactly as written, including the @. Each stands for a
  saved character, location or prop that is inserted later, so don't describe what it looks like.
- If the request lists shot direction that is already handled (camera moves, framing, lens, lighting,
  palette, genre), leave those topics out of the prompt; they are appended separately.
- Follow the dialect notes and stay under the character limit.
- Return only the prompt text in `prompt`, with no preamble, quotes, headings or lists.
- In `changes`, list up to three very short notes (under 8 words each) on what you added."""


class EnhancedPrompt(BaseModel):
    prompt: str = Field(description="The rewritten prompt, ready to send to the model")
    changes: list[str] = Field(default_factory=list, description="Up to three short notes on what was added")


def _covered(direction: ShotDirection | None) -> list[str]:
    if direction is None:
        return []
    d = direction
    out = []
    if d.camera_moves:
        out.append("camera movement")
    if d.shot_size or d.angle:
        out.append("framing and angle")
    if d.lens or d.aperture or d.camera_body:
        out.append("lens and camera")
    if d.lighting or d.palette:
        out.append("lighting and colour palette")
    if d.genre or d.era or d.pacing or d.emotion:
        out.append("genre, era, pacing and mood")
    return out


def build_request(spec: ModelSpec, mode_id: str, prompt: str, direction: ShotDirection | None) -> str:
    mode = spec.mode(mode_id)
    has_audio = spec.param("generate_audio") is not None or "audio" in spec.tags
    lines = [
        f"Target model: {spec.name} ({spec.output_type}), mode: {mode.label if mode else mode_id}.",
        f"Dialect notes: {DIALECT_STYLE.get(spec.prompt.dialect, DEFAULT_STYLE)}",
        f"Character limit: {min(spec.prompt.max_chars, 1500)}.",
        "The model can generate sound." if has_audio and spec.output_type == "video" else "",
        ("The clip starts from the user's image, so describe what happens next rather than re-describing it."
         if mode and mode.slot("start_image") else ""),
        f"Already handled by shot direction: {', '.join(c)}." if (c := _covered(direction)) else "",
        f"\nUser's idea:\n{prompt.strip()}",
    ]
    return "\n".join(line for line in lines if line)


def _mock(spec: ModelSpec, prompt: str) -> EnhancedPrompt:
    base = prompt.strip().rstrip(".")
    if spec.output_type == "image":
        extra = "sharp focus, rich surface texture, balanced composition, natural light"
    else:
        extra = "natural motion with believable weight, fine surface detail, subtle movement in the background"
        if spec.param("generate_audio") is not None:
            extra += ", ambient sound that matches the scene"
    if base.endswith(extra):  # already enhanced; don't stack the same detail again
        extra = ""
    return EnhancedPrompt(
        prompt=(f"{base}, {extra}." if extra else f"{base}.")[: spec.prompt.max_chars],
        changes=["Mock enhancement: added texture and motion detail",
                 "Set LLM_MODE=live and ANTHROPIC_API_KEY for Claude"],
    )


_cache: OrderedDict[str, LLMResult[EnhancedPrompt]] = OrderedDict()
CACHE_SIZE = 256


def _key(model: str, spec: ModelSpec, mode_id: str, prompt: str, direction: ShotDirection | None) -> str:
    raw = json.dumps([model, spec.id, mode_id, prompt.strip(), direction.model_dump() if direction else None],
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


async def enhance(
    session: AsyncSession, user_id: uuid.UUID, spec: ModelSpec, mode_id: str, prompt: str,
    direction: ShotDirection | None = None,
) -> tuple[LLMResult[EnhancedPrompt], list[str]]:
    """Returns the result and warnings (e.g. a dropped @mention)."""
    model = get_settings().llm_fast_model
    key = _key(model, spec, mode_id, prompt, direction)
    if key in _cache:
        _cache.move_to_end(key)
        cached = _cache[key]
        return LLMResult(output=cached.output, model=cached.model, mock=cached.mock), []

    result = await structured(
        session, user_id, purpose="enhance", model=model, system=SYSTEM,
        prompt=build_request(spec, mode_id, prompt, direction), output=EnhancedPrompt,
        mock=lambda: _mock(spec, prompt), effort="low", max_tokens=4000,
    )
    out = result.output
    out.prompt = " ".join(out.prompt.split())[: spec.prompt.max_chars]
    out.changes = out.changes[:3]
    warnings = [f"@{m} was dropped; add it back if you need it"
                for m in mentions(prompt) if m not in mentions(out.prompt)]

    _cache[key] = result
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return result, warnings
