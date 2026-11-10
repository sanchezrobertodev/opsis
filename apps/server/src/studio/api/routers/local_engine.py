"""What your local ComfyUI offers: which local models it can run now (and why not), and the options of params it
lists (`comfy_choices`: its LoRA files)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.db.models import User
from studio.domain.generations import routing_env
from studio.providers.comfy_local import ComfyLocalAdapter
from studio.providers.local_setup import LOCAL_PROVIDER
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
