"""Local ComfyUI model storage preferences."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from studio.api.deps import current_user
from studio.config import get_settings
from studio.db.models import User
from studio.providers.model_storage import save_directory, storage_info

router = APIRouter(prefix="/local-engine/model-storage", tags=["local engine"])


class ModelStorageOut(BaseModel):
    available: bool
    message: str | None = None
    directory: str | None = None
    active_directory: str | None = None
    libraries: list[str] = Field(default_factory=list)
    free_bytes: int | None = None
    restart_required: bool | None = None


class ModelStorageUpdate(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)


async def status() -> ModelStorageOut:
    settings = get_settings()
    try:
        info = await asyncio.to_thread(storage_info, settings)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        return ModelStorageOut(available=False, message=str(exc))
    active = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(settings.comfyui_url.rstrip("/") + "/system_stats")
            response.raise_for_status()
            argv = response.json().get("system", {}).get("argv", [])
            if "--models-directory" in argv:
                active = str(Path(argv[argv.index("--models-directory") + 1]).resolve())
    except (httpx.HTTPError, ValueError, IndexError, TypeError):
        pass
    return ModelStorageOut(available=True, **info, active_directory=active,
                           restart_required=(Path(active) != Path(info["directory"])) if active else None)


@router.get("", response_model=ModelStorageOut)
async def get_model_storage() -> ModelStorageOut:
    return await status()


@router.patch("", response_model=ModelStorageOut)
async def update_model_storage(body: ModelStorageUpdate, _: User = Depends(current_user)) -> ModelStorageOut:
    try:
        await asyncio.to_thread(save_directory, get_settings(), body.directory)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await status()
