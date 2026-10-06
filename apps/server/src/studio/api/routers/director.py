from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.api.schemas import CompileIn, CompileOut, DirectorLibraryOut
from studio.db.models import User
from studio.director.compiler import compile_prompt
from studio.director.elements import resolve_mentions
from studio.director.library import get_library
from studio.registry.loader import get_registry

router = APIRouter(prefix="/director", tags=["director"])


@router.get("/library", response_model=DirectorLibraryOut)
async def library() -> DirectorLibraryOut:
    """Camera moves and cinema looks for the director panel."""
    return DirectorLibraryOut.model_validate(get_library().model_dump())


@router.post("/compile", response_model=CompileOut)
async def compile_preview(
    body: CompileIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CompileOut:
    """Preview the exact prompt a model will receive for this prompt and direction. Free; no generation."""
    spec = get_registry().model(body.model_id)
    if spec is None:
        raise HTTPException(404, f"unknown model {body.model_id}")
    if body.mode is not None and spec.mode(body.mode) is None:
        raise HTTPException(422, f"{spec.name} has no mode {body.mode!r}")
    prompt, warnings = body.prompt, []
    if body.mode is not None:
        media = [{"role": m.role, "asset_id": str(m.asset_id)} for m in body.media]
        mention = await resolve_mentions(session, user.id, spec, body.mode, body.prompt, media)
        prompt, warnings = mention.prompt, mention.warnings
    c = compile_prompt(spec, prompt, body.direction)
    return CompileOut(prompt=c.prompt, warnings=[*warnings, *c.warnings], dialect=c.dialect,
                      max_chars=spec.prompt.max_chars)
