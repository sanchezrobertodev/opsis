"""Claude steps, keyed by the step input's `op`. They run in the `pipeline.step` task like local steps, and
their ledger charges commit with the step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget.guard import BudgetBlocked
from studio.config import get_settings
from studio.db.models import PipelineRun, PipelineStep
from studio.director.elements import mentions
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import plan_blocks, song_text, write_script, write_song
from studio.llm.client import LLMError, structured
from studio.pipelines.ops import StepFailed
from studio.pipelines.recipes.music_video import plan_shots
from studio.pipelines.recipes.vlog import write_vlog
from studio.providers.mock import strip_tags

LLMOp = Callable[[AsyncSession, PipelineRun, PipelineStep], Awaitable[dict[str, Any]]]


def _guarded(fn: LLMOp) -> LLMOp:
    async def run(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
        try:
            return await fn(session, run, step)
        except LLMError as exc:
            raise StepFailed(exc.message) from exc
        except BudgetBlocked as exc:
            raise StepFailed(f"Claude wasn't called: {exc}") from exc

    return run


async def faceless_script(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    inp = step.input
    theme = get_catalog().theme(inp["theme"])
    if theme is None:
        raise StepFailed(f"unknown theme {inp['theme']!r}")
    res = await write_script(session, run.user_id, topic=inp["topic"], theme=theme, minutes=inp["minutes"],
                             language=inp.get("language", "English"), notes=inp.get("notes", ""))
    return {"title": res.output.title, "script": res.output.narration, "model": res.model,
            "cost_usd": float(res.cost_usd)}


async def faceless_plan(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    inp = step.input
    style = get_catalog().style(inp["style"])
    if style is None:
        raise StepFailed(f"unknown style {inp['style']!r}")
    res = await plan_blocks(session, run.user_id, script=inp["script"], title=inp["title"], style=style,
                            fmt=inp["format"])
    plan = res.output
    return {"world": plan.world, "roster": [r.model_dump() for r in plan.roster],
            "blocks": [b.model_dump() for b in plan.blocks], "model": res.model, "cost_usd": float(res.cost_usd)}


async def faceless_song(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    inp = step.input
    theme = get_catalog().theme(inp["theme"])
    if theme is None:
        raise StepFailed(f"unknown theme {inp['theme']!r}")
    res = await write_song(session, run.user_id, topic=inp["topic"], theme=theme, minutes=inp["minutes"],
                           language=inp.get("language", "English"), notes=inp.get("notes", ""))
    song = res.output
    return {"title": song.title, "script": song_text(song), "song": song.model_dump(), "model": res.model,
            "cost_usd": float(res.cost_usd)}


async def vlog_script(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    inp = step.input
    res = await write_vlog(session, run.user_id, idea=inp["idea"], shots=inp["shots"], name=inp.get("name", ""),
                           language=inp.get("language", "English"), notes=inp.get("notes", ""))
    return {"title": res.output.title, "shots": [s.model_dump() for s in res.output.shots], "model": res.model,
            "cost_usd": float(res.cost_usd)}


async def music_video_plan(session: AsyncSession, run: PipelineRun, step: PipelineStep) -> dict[str, Any]:
    inp = step.input
    res = await plan_shots(session, run.user_id, sections=inp["sections"], idea=inp.get("idea", ""),
                           star=bool(inp.get("star")))
    return {"world": res.output.world, "shots": [s.model_dump() for s in res.output.shots], "model": res.model,
            "cost_usd": float(res.cost_usd)}


class Reworded(BaseModel):
    prompts: list[str] = Field(description="Each prompt reworded, in the same order and number; one that shouldn't "
                                           "be made at all stays exactly as given")
    changed: str = Field(description="One short sentence on what you changed and why")


REWORD_SYSTEM = """A video or image model refused a request with a content-policy error. Most such refusals in this
studio are false positives: ordinary film scenes whose wording reads as more graphic than they are.

Reword each prompt so it shows the same scene, action, framing and mood with gentler wording: describe peril, injury,
weapons or conflict without graphic detail, drop real people's and brands' names, and avoid wording that could read
as sexual, as involving minors, or as drug or medical detail. Keep every @handle exactly as written, keep the
language, stay within each prompt's character limit, and don't add anything the scene didn't have. Return as many
prompts as you were given, in order.

If a prompt asks for something that shouldn't be made at all, return it unchanged and say so."""


async def reword(session: AsyncSession, run: PipelineRun, prompts: list[str], reason: str) -> Reworded:
    """Gentler wordings of the prompts of a take a provider refused (one, or a multi-shot take's shots), each kept
    to its original's length (which the model already accepted) and @mentions. Fails when Claude changes nothing or
    drops a mention, so the step fails the way it would have without the second try."""
    listed = "\n\n".join(f"Prompt {i} (at most {len(p)} characters):\n{p}" for i, p in enumerate(prompts, 1))
    try:
        res = await structured(
            session, run.user_id, purpose="pipeline.reword", model=get_settings().llm_fast_model,
            system=REWORD_SYSTEM, prompt=f"The refusal: {reason[:500]}\n\n{listed}", output=Reworded,
            # the mock drops its failure tags, so a test sees the second try succeed
            mock=lambda: Reworded(prompts=[f"{strip_tags(p)} Gently worded." for p in prompts], changed="mock"),
            effort="low", max_tokens=4000)
    except LLMError as exc:
        raise StepFailed(f"Claude couldn't reword it: {exc.message}") from exc
    except BudgetBlocked as exc:
        raise StepFailed(f"Claude wasn't called: {exc}") from exc
    out = res.output
    if len(out.prompts) != len(prompts):
        raise StepFailed(f"Claude returned {len(out.prompts)} prompts for {len(prompts)}")
    new = [_within(n.strip() or old, len(old)) for n, old in zip(out.prompts, prompts, strict=True)]
    if all(n == old.strip() for n, old in zip(new, prompts, strict=True)):
        raise StepFailed(f"Claude kept the prompt as it was: {out.changed}")
    for n, old in zip(new, prompts, strict=True):
        if missing := [m for m in mentions(old) if m not in mentions(n)]:
            raise StepFailed(f"the reworded prompt dropped {', '.join('@' + m for m in missing)}")
    return Reworded(prompts=new, changed=out.changed)


def _within(text: str, limit: int) -> str:
    """Cut a rewording that came back longer than its original at the last whole word that fits."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit].isspace() or " " not in cut:  # the limit falls between words (or inside the only one)
        return cut.rstrip(" ,;:")
    return cut[:cut.rfind(" ")].rstrip(" ,;:")


LLM_OPS: dict[str, LLMOp] = {
    "faceless_script": _guarded(faceless_script),
    "faceless_plan": _guarded(faceless_plan),
    "faceless_song": _guarded(faceless_song),
    "vlog_script": _guarded(vlog_script),
    "music_video_plan": _guarded(music_video_plan),
}
