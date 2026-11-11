"""What your local ComfyUI offers: which local models it can run now (and why not), and the options of params it
lists (`comfy_choices`: its LoRA files). When that ComfyUI's folders are on this computer, Settings can also set the
models up: the same plan and steps as scripts/setup_local.py, one setup at a time in a background thread."""

from __future__ import annotations

import os
import threading
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.config import get_settings
from studio.db.models import User
from studio.domain.generations import routing_env
from studio.providers.comfy_local import ComfyLocalAdapter
from studio.providers.local_setup import (
    LOCAL_PROVIDER,
    Comfy,
    Plan,
    carry_out,
    describe,
    on_this_computer,
    plan,
    python_problem,
)
from studio.providers.local_setup import local_models as setup_models
from studio.registry.loader import get_registry
from studio.registry.routing import runs_locally

router = APIRouter(prefix="/local-engine", tags=["local engine"])


class ChoicesOut(BaseModel):
    available: bool
    options: list[str] = Field(default_factory=list)
    message: str | None = None


class LocalModelOut(BaseModel):
    id: str
    name: str
    ready: bool
    reason: str | None = Field(None, description="Why a running ComfyUI can't run it yet, naming the fix")


@router.get("/models", response_model=list[LocalModelOut])
async def local_models(session: AsyncSession = Depends(get_session),
                       user: User = Depends(current_user)) -> list[LocalModelOut]:
    """Every model your own ComfyUI can run, and whether it can now (scripts/setup_local.py sets them up)."""
    reg = get_registry()
    env = await routing_env(session, reg, user.id)
    out = []
    for spec in reg.models.values():
        routes = [r for r in spec.routes if r.provider == LOCAL_PROVIDER and r.enabled]
        if not routes:
            continue
        ready = any(runs_locally(reg, spec, m.id, env) for m in spec.modes)
        reason = None if ready else next((why for r in routes for m in r.modes if (why := env.blocked(r, m))), None)
        out.append(LocalModelOut(id=spec.id, name=spec.name, ready=ready, reason=reason))
    return sorted(out, key=lambda m: (not m.ready, m.name))


@router.get("/choices", response_model=ChoicesOut)
async def choices(
    field: str = Query(pattern=r"^\w+\.\w+$", description="Node.input, e.g. LoraLoaderModelOnly.lora_name"),
    user: User = Depends(current_user),
) -> ChoicesOut:
    adapter = ComfyLocalAdapter()
    if not adapter.base:
        return ChoicesOut(available=False, message="Set COMFYUI_URL to use your own ComfyUI.")
    node, input_ = field.split(".")
    options = await adapter.choices(node, input_)
    if options is None:
        return ChoicesOut(available=False, message=f"ComfyUI at {adapter.base} didn't list them. Is it running?")
    return ChoicesOut(available=True, options=[str(o) for o in options])


class SetupModelOut(BaseModel):
    id: str
    state: Literal["ready", "needs", "too_big"]
    needs: str = Field("", description="What it still needs, e.g. the X node pack, 5 model files, 23.0 GB")
    packs: list[str] = Field(default_factory=list)
    files: int = 0
    bytes: int = Field(0, description="Download size")
    free_bytes: int | None = Field(None, description="Free disk where its files go")
    room: bool = True


class SetupJobOut(BaseModel):
    state: Literal["running", "done", "failed"]
    model_ids: list[str]
    step: str | None = None
    done_bytes: int = 0
    total_bytes: int = 0
    failed: list[str] = Field(default_factory=list)
    restart: bool = Field(False, description="A node pack was added: ComfyUI loads it when restarted")


class SetupOut(BaseModel):
    available: bool
    reason: str | None = Field(None, description="Why models can't be set up from here (then run the script)")
    python_problem: str | None = Field(None, description="Why node packs can't be installed")
    models: list[SetupModelOut] = Field(default_factory=list)
    job: SetupJobOut | None = None


class SetupIn(BaseModel):
    model_ids: list[str] = Field(min_length=1, max_length=50)


# shortcut: one setup at a time per API process, kept in memory; a restart of the API stops it, and the next setup
# resumes its downloads from their .part files
_job: dict[str, Any] = {}
_plan: Plan | None = None
_lock = threading.Lock()


def _job_out() -> SetupJobOut | None:
    if not _job:
        return None
    done = _plan.got_bytes() if _plan else 0
    return SetupJobOut(**_job, done_bytes=done, total_bytes=_plan.bytes if _plan else 0)


def _here() -> tuple[Comfy | str, list]:
    return on_this_computer(get_settings().comfyui_url), setup_models(get_registry())


def _status() -> SetupOut:
    comfy, models = _here()
    if isinstance(comfy, str):
        return SetupOut(available=False, reason=comfy, job=_job_out())
    out = []
    for m in models:
        state, needs = describe(m, comfy)
        work = plan([m], comfy)
        out.append(SetupModelOut(id=m.id, state=state.replace(" ", "_"), needs=needs, packs=[p.name for p in work.packs],
                                 files=len(work.jobs), bytes=work.bytes, free_bytes=work.free_bytes(), room=work.room()))
    needs_pack = any(m.packs for m in out if m.state == "needs")
    return SetupOut(available=True, models=out, job=_job_out(),
                    python_problem=python_problem(comfy, comfy.python()) if needs_pack else None)


def _start(ids: list[str]) -> SetupJobOut:
    global _plan
    comfy, models = _here()
    if isinstance(comfy, str):
        raise HTTPException(409, f"Can't set up from here: {comfy}. Run scripts/setup_local.py on that computer.")
    by_id = {m.id: m for m in models}
    if unknown := [i for i in ids if i not in by_id]:
        raise HTTPException(422, f"No local model {', '.join(unknown)}")
    chosen = [by_id[i] for i in dict.fromkeys(ids)]
    if too_big := [m.name for m in chosen if not comfy.fits(m)]:
        raise HTTPException(409, f"{', '.join(too_big)} won't run on this GPU")
    work = plan(chosen, comfy)
    if not work.packs and not work.jobs:
        raise HTTPException(409, "Already set up")
    python = comfy.python()
    if work.packs and (problem := python_problem(comfy, python)):
        raise HTTPException(409, f"Can't install node packs: {problem}. Run scripts/setup_local.py with --python.")
    if not work.room():
        raise HTTPException(409, f"Not enough free disk: the files need {work.bytes / 1e9:.1f} GB, "
                                 f"{(work.free_bytes() or 0) / 1e9:.0f} GB free")
    with _lock:
        if _job.get("state") == "running":
            raise HTTPException(409, "A setup is already running")
        _plan = work
        _job.clear()
        _job.update(state="running", model_ids=[m.id for m in chosen], step=None, failed=[], restart=bool(work.packs))
    threading.Thread(target=_run, args=(work, comfy, python), name="local-setup", daemon=True).start()
    return _job_out()  # type: ignore[return-value]


def _run(work: Plan, comfy: Comfy, python) -> None:
    try:
        failed = carry_out(work, comfy, python, say=lambda step: _job.update(step=step))
    except Exception as exc:  # report it in Settings, never leave the setup "running"
        failed = [f"{type(exc).__name__}: {exc}"]
    if any(code in msg for msg in failed for code in ("401", "403")) and not os.environ.get("HF_TOKEN"):
        failed.append("A gated file: accept its license on Hugging Face, set HF_TOKEN where Opsis runs, and retry.")
    _job.update(state="failed" if failed else "done", step=None, failed=failed)


@router.get("/setup", response_model=SetupOut)
async def setup_status(_: User = Depends(current_user)) -> SetupOut:
    """Whether the local models can be set up from here, and what each still needs (packs, files, disk)."""
    return await anyio.to_thread.run_sync(_status)


@router.post("/setup", response_model=SetupJobOut)
async def start_setup(body: SetupIn, _: User = Depends(current_user)) -> SetupJobOut:
    """Install what the models need into the ComfyUI on this computer: their node packs (at the commits their graphs
    were checked with) and model files (from the manifest). Follow it with GET /local-engine/setup/job."""
    return await anyio.to_thread.run_sync(_start, body.model_ids)


@router.get("/setup/job", response_model=SetupJobOut | None)
async def setup_job(_: User = Depends(current_user)) -> SetupJobOut | None:
    return await anyio.to_thread.run_sync(_job_out)
