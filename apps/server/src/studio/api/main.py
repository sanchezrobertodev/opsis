from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio import keys as provider_keys
from studio.api import mcp_tools
from studio.api.routers import (
    assets,
    director,
    elements,
    faceless,
    generations,
    keys,
    llm,
    local_engine,
    misc,
    model_storage,
    pipelines,
    presets,
    projects,
    registry,
    renders,
    templates,
)
from studio.api.routers.assets import MAX_UPLOAD_BYTES
from studio.api.schemas import CandidateOut, ConfirmationProblem, ValidationProblem
from studio.api.sse import hub
from studio.config import get_settings
from studio.domain.fsm import IllegalTransition
from studio.domain.generations import ConfirmationRequired, GenerationRequestError
from studio.registry.loader import get_registry_holder
from studio.worker.app import app as queue_app

log = logging.getLogger("studio.api")


async def _watch_registry() -> None:
    """Tells open pages when the registry changes. `current` re-reads changed files at most once per `check_every`,
    yours too, in a folder made since the start; a broken file keeps the last good copy."""
    holder = get_registry_holder()
    seen = holder.reloads
    while True:
        await asyncio.sleep(holder.check_every)
        reg = holder.current
        if holder.reloads != seen:
            seen = holder.reloads
            hub.publish({
                "id": None, "topic": "system",
                "type": "system.registry_error" if holder.last_error else "system.registry_reloaded",
                "payload": {"hash": reg.hash, "errors": holder.last_error},
            })


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    reg = get_registry_holder().current  # fail fast on a broken registry
    log.info("registry %s: %d models, provider mode %s", reg.hash, len(reg.models), get_settings().provider_mode)
    await hub.start()
    watcher = asyncio.create_task(_watch_registry(), name="registry-watch")
    key_refresh = asyncio.create_task(provider_keys.refresh_forever(), name="key-refresh")
    async with queue_app.open_async(), mcp_tools.running():
        yield
    for task in (watcher, key_refresh):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await hub.stop()


app = FastAPI(
    title="Opsis API",
    version="0.1.0",
    lifespan=lifespan,
    description="Self-hosted AI video studio: model registry, generations, assets and live events.",
)
MAX_BODY_BYTES = MAX_UPLOAD_BYTES + (16 << 20)  # the largest upload plus the form around it
MAX_JSON_BYTES = 16 << 20  # any other body, which is read into memory whole
# the routes that take files, which the form parser spools to disk; any other route reads a body whole, whatever its type
FILE_ROUTES = re.compile(r"/uploads|/projects/[^/]+/renders/[^/]+/frames")
_web = get_settings().web_origin
# 127.0.0.1 is the same dev web app as localhost; a deployed API trusts only its own web app
ORIGINS = [_web, *(["http://127.0.0.1:3000"] if _web.startswith("http://localhost") else [])]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@app.middleware("http")
async def _guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Refuse what another website makes a browser send, and a body by its declared size before it's read: the form
    parser spools every file to disk first and a JSON body is read whole, so a check in the route comes too late."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        # CORS only hides the answer from a page elsewhere, not the form it posts. The API's own pages (/docs) may;
        # /mcp checks its callers itself
        if (origin is not None and origin not in ORIGINS and origin != str(request.base_url).rstrip("/")
                and request.url.path != "/mcp"):
            return JSONResponse(status_code=403, content={"detail": "requests from other websites are refused"})
    if request.method in ("POST", "PUT", "PATCH"):
        length = request.headers.get("content-length")
        form = request.headers.get("content-type", "").strip().lower().startswith("multipart/form-data")
        # a chunked body has no size to check up front
        if "transfer-encoding" in request.headers or (length is None and form):
            return JSONResponse(status_code=411, content={"detail": "send the body with a Content-Length"})
        big = form and FILE_ROUTES.fullmatch(request.url.path) is not None
        if length is not None and (not length.isdigit() or int(length) > (MAX_BODY_BYTES if big else MAX_JSON_BYTES)):
            return JSONResponse(status_code=413, content={"detail": "file too large" if big else "body too large"})
    return await call_next(request)


# DNS rebinding: a page whose own name points at this machine is same-origin with the API, so only this machine's
# names and the web app's are answered (any port). Never a bare name like "test": the OS may ask the network for it.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(mcp_tools.hosts()))


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
app.include_router(keys.router)
app.include_router(model_storage.router)
app.include_router(local_engine.router)
app.router.routes.append(mcp_tools.route)  # the MCP server, for the Claude apps
