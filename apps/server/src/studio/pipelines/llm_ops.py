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
from studio.faceless.writer import plan_blocks, write_script
from studio.llm.client import LLMError, structured
from studio.pipelines.ops import StepFailed
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


class Reworded(BaseModel):
    prompt: str = Field(description="The prompt reworded, or exactly as given when it shouldn't be made at all")
    changed: str = Field(description="One short sentence on what you changed and why")


REWORD_SYSTEM = """A video or image model refused a prompt with a content-policy error. Most such refusals in this
studio are false positives: ordinary film scenes whose wording reads as more graphic than they are.

Reword the prompt so it shows the same scene, action, framing and mood with gentler wording: describe peril, injury,
weapons or conflict without graphic detail, drop real people's and brands' names, and avoid wording that could read
as sexual, as involving minors, or as drug or medical detail. Keep every @handle exactly as written, keep the
language and roughly the length, and don't add anything the scene didn't have.

If the prompt asks for something that shouldn't be made at all, return it unchanged and say so."""


async def reword(session: AsyncSession, run: PipelineRun, prompt: str, reason: str) -> Reworded:
    """A gentler wording of a prompt a provider refused, keeping its @mentions. Fails when Claude keeps it as is
    or drops a mention, so the step fails the way it would have without the second try."""
    try:
        res = await structured(
            session, run.user_id, purpose="pipeline.reword", model=get_settings().llm_fast_model,
            system=REWORD_SYSTEM, prompt=f"The refusal: {reason[:500]}\n\nThe prompt:\n{prompt}", output=Reworded,
            # the mock drops its failure tags, so a test sees the second try succeed
            mock=lambda: Reworded(prompt=f"{strip_tags(prompt)} Gently worded.", changed="mock rewording"),
            effort="low", max_tokens=2000)
    except LLMError as exc:
        raise StepFailed(f"Claude couldn't reword it: {exc.message}") from exc
    except BudgetBlocked as exc:
        raise StepFailed(f"Claude wasn't called: {exc}") from exc
    out = res.output
    new = out.prompt.strip()
    if not new or new == prompt.strip():
        raise StepFailed(f"Claude kept the prompt as it was: {out.changed}")
    missing = [m for m in mentions(prompt) if m not in mentions(new)]
    if missing:
        raise StepFailed(f"the reworded prompt dropped {', '.join('@' + m for m in missing)}")
    return Reworded(prompt=new[:len(prompt) + 500], changed=out.changed)


LLM_OPS: dict[str, LLMOp] = {
    "faceless_script": _guarded(faceless_script),
    "faceless_plan": _guarded(faceless_plan),
}
