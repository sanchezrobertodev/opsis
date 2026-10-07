"""Claude steps, keyed by the step input's `op`. They run in the `pipeline.step` task like local steps, and
their ledger charges commit with the step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from studio.budget.guard import BudgetBlocked
from studio.db.models import PipelineRun, PipelineStep
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import plan_blocks, write_script
from studio.llm.client import LLMError
from studio.pipelines.ops import StepFailed

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


LLM_OPS: dict[str, LLMOp] = {
    "faceless_script": _guarded(faceless_script),
    "faceless_plan": _guarded(faceless_plan),
}
