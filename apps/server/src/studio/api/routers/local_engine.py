"""What your local ComfyUI offers, for params whose options it lists (`comfy_choices`: its LoRA files)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from studio.api.deps import current_user
from studio.db.models import User
from studio.providers.comfy_local import ComfyLocalAdapter

router = APIRouter(prefix="/local-engine", tags=["local engine"])


class ChoicesOut(BaseModel):
    available: bool
    options: list[str] = Field(default_factory=list)
    message: str | None = None


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
