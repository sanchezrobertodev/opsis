from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from studio.api.routers import (
    assets,
    director,
    elements,
    faceless,
    generations,
    llm,
    misc,
    pipelines,
    presets,
    projects,
    registry,
    renders,
    templates,
)
from studio.api.schemas import CandidateOut, ConfirmationProblem, ValidationProblem
from studio.api.sse import hub
from studio.config import get_settings
from studio.domain.fsm import IllegalTransition
from studio.domain.generations import ConfirmationRequired, GenerationRequestError
from studio.registry.loader import get_registry_holder
from studio.worker.app import app as queue_app

log = logging.getLogger("studio.api")


async def _watch_registry() -> None:
    """Dev convenience: edit registry YAML and the API picks it up; a broken file keeps the last good copy."""
    from watchfiles import awatch

    holder = get_registry_holder()
    async for _ in awatch(holder.root):
        ok = holder.reload()
        hub.publish({
            "id": None, "topic": "system",
            "type": "system.registry_reloaded" if ok else "system.registry_error",
            "payload": {"hash": holder.current.hash, "errors": holder.last_error},
        })


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    reg = get_registry_holder().current  # fail fast on a broken registry
    log.info("registry %s: %d models, provider mode %s", reg.hash, len(reg.models), get_settings().provider_mode)
    await hub.start()
    watcher = asyncio.create_task(_watch_registry(), name="registry-watch")
    async with queue_app.open_async():
        yield
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher
    await hub.stop()


app = FastAPI(
    title="AI Media Studio API",
    version="0.1.0",
    lifespan=lifespan,
    description="Personal AI video studio: model registry, generations, assets and live events.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin, "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@app.exception_handler(GenerationRequestError)
async def _invalid(_: Request, exc: GenerationRequestError) -> JSONResponse:
    body = ValidationProblem(errors=exc.errors, candidates=[CandidateOut(**c) for c in exc.candidates])
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


@app.exception_handler(ConfirmationRequired)
async def _confirm(_: Request, exc: ConfirmationRequired) -> JSONResponse:
    body = ConfirmationProblem(message=str(exc), estimate_usd=float(exc.estimate_usd), route_id=exc.route_id,
                               threshold_usd=float(exc.threshold))
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))


@app.exception_handler(IllegalTransition)
async def _illegal(_: Request, exc: IllegalTransition) -> JSONResponse:
    return JSONResponse(status_code=409, content={"code": "illegal_transition", "message": str(exc)})


app.include_router(registry.router)
app.include_router(director.router)
app.include_router(elements.router)
app.include_router(presets.router)
app.include_router(templates.router)
app.include_router(llm.router)
app.include_router(pipelines.router)
app.include_router(faceless.router)
app.include_router(projects.router)
app.include_router(renders.router)
app.include_router(generations.router)
app.include_router(assets.router)
app.include_router(misc.router)
