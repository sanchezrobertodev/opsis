"""Faceless Studio: themes and styles, topic ideas from Claude, and channels (saved episode settings).

Episodes are ordinary `faceless` recipe runs (POST /pipelines); a channel's episodes are the runs whose input
names it (GET /pipelines?channel_id=...).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.deps import current_user, get_session
from studio.budget.guard import BudgetBlocked
from studio.db.models import Channel, PipelineRun, User
from studio.faceless.catalog import get_catalog
from studio.faceless.writer import suggest_topics
from studio.llm.client import LLMError, LLMUnavailable
from studio.pipelines.recipes.faceless import FacelessInput
from studio.registry.loader import get_registry

router = APIRouter(prefix="/faceless", tags=["faceless"])

# Episode fields a channel may preset; the topic, script and title belong to each episode.
DEFAULT_FIELDS = sorted(set(FacelessInput.model_fields) - {"topic", "script", "title", "channel_id", "theme", "style"})


class Option(BaseModel):
    id: str
    label: str


class ThemeOut(BaseModel):
    id: str
    label: str
    summary: str
    voice_id: str


class StyleOut(BaseModel):
    id: str
    label: str
    summary: str
    prompt: str
    caption: dict[str, Any]


class CatalogOut(BaseModel):
    themes: list[ThemeOut]
    styles: list[StyleOut]
    voices: list[Option] = Field(description="Voices of the default narration model")
    defaults: dict[str, Any] = Field(description="Episode settings a new channel starts from")


@router.get("/catalog", response_model=CatalogOut)
async def catalog() -> CatalogOut:
    cat = get_catalog()
    voice = get_registry().model(FacelessInput.model_fields["voice_model"].default)
    param = voice.param("voice_id") if voice else None
    voices = [Option(id=str(o), label=param.labels.get(str(o), str(o))) for o in getattr(param, "options", [])]
    defaults = FacelessInput(topic="x").model_dump(mode="json", include=set(DEFAULT_FIELDS))
    return CatalogOut(
        themes=[ThemeOut(id=t.id, label=t.label, summary=t.summary, voice_id=t.voice_id) for t in cat.themes],
        styles=[StyleOut(id=s.id, label=s.label, summary=s.summary, prompt=s.prompt,
                         caption=s.caption.model_dump()) for s in cat.styles],
        voices=voices, defaults=defaults)


# ---------------------------------------------------------------- topic ideas


class TopicsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str
    channel_id: uuid.UUID | None = None
    notes: str = Field("", max_length=1000)


class TopicOut(BaseModel):
    title: str
    angle: str


class TopicsOut(BaseModel):
    topics: list[TopicOut]
    model: str
    mock: bool
    cost_usd: float


@router.post("/topics", response_model=TopicsOut,
             responses={402: {"description": "Spending limit"}, 502: {"description": "Claude failed"},
                        503: {"description": "No Anthropic key"}})
async def topics(body: TopicsIn, session: AsyncSession = Depends(get_session),
                 user: User = Depends(current_user)) -> TopicsOut:
    """Four topic ideas for a channel's theme. Billed to your budget in live LLM mode."""
    theme = get_catalog().theme(body.theme)
    if theme is None:
        raise HTTPException(404, f"unknown theme {body.theme!r}")
    channel = await _channel(session, user, body.channel_id) if body.channel_id else None
    notes = "\n".join(n for n in (channel.notes if channel else "", body.notes) if n)
    try:
        result = await suggest_topics(session, user.id, theme=theme, channel=channel.name if channel else "", notes=notes)
    except BudgetBlocked as exc:
        raise HTTPException(402, f"Asking Claude would go over your spending limit: {exc}") from exc
    except LLMUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    except LLMError as exc:
        await session.commit()  # a refusal was still billed
        raise HTTPException(502, exc.message) from exc
    await session.commit()
    return TopicsOut(topics=[TopicOut(title=t.title, angle=t.angle) for t in result.output.topics],
                     model=result.model, mock=result.mock, cost_usd=float(result.cost_usd))


# ---------------------------------------------------------------- channels


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    theme: str
    style: str
    notes: str = Field("", max_length=2000, description="Standing guidance for the writer")
    defaults: dict[str, Any] = Field(default_factory=dict, description=f"Episode settings: {', '.join(DEFAULT_FIELDS)}")


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=120)
    theme: str | None = None
    style: str | None = None
    notes: str | None = Field(None, max_length=2000)
    defaults: dict[str, Any] | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    name: str
    theme: str
    style: str
    notes: str
    defaults: dict[str, Any]
    episodes: int
    last_episode_at: datetime | None
    created_at: datetime


def _check(theme: str | None, style: str | None, defaults: dict[str, Any] | None) -> dict[str, Any] | None:
    cat = get_catalog()
    errors = []
    if theme is not None and cat.theme(theme) is None:
        errors.append(f"unknown theme {theme!r}")
    if style is not None and cat.style(style) is None:
        errors.append(f"unknown style {style!r}")
    if defaults is not None:
        unknown = sorted(set(defaults) - set(DEFAULT_FIELDS))
        if unknown:
            errors.append(f"not an episode setting: {', '.join(unknown)}")
        else:
            try:
                defaults = FacelessInput.model_validate({**defaults, "topic": "x"}).model_dump(
                    mode="json", include=set(defaults))
            except ValueError as exc:
                errors.append(str(exc).splitlines()[0])
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return defaults


async def _channel(session: AsyncSession, user: User, channel_id: uuid.UUID) -> Channel:
    ch = await session.get(Channel, channel_id)
    if ch is None or ch.user_id != user.id or ch.deleted_at is not None:
        raise HTTPException(404, "channel not found")
    return ch


async def _out(session: AsyncSession, channels: list[Channel]) -> list[ChannelOut]:
    key = PipelineRun.input["channel_id"].astext
    stats = {cid: (n, last) for cid, n, last in (await session.execute(
        select(key, func.count(), func.max(PipelineRun.created_at))
        .where(PipelineRun.recipe == "faceless", key.in_([str(c.id) for c in channels]))
        .group_by(key))).all()} if channels else {}
    return [ChannelOut(id=c.id, name=c.name, theme=c.theme, style=c.style, notes=c.notes, defaults=c.defaults,
                       episodes=stats.get(str(c.id), (0, None))[0], last_episode_at=stats.get(str(c.id), (0, None))[1],
                       created_at=c.created_at) for c in channels]


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(session: AsyncSession = Depends(get_session),
                        user: User = Depends(current_user)) -> list[ChannelOut]:
    rows = (await session.execute(select(Channel).where(Channel.user_id == user.id, Channel.deleted_at.is_(None))
                                  .order_by(Channel.created_at.desc()))).scalars().all()
    return await _out(session, list(rows))


@router.post("/channels", response_model=ChannelOut, status_code=201, responses={422: {"description": "Bad settings"}})
async def create_channel(body: ChannelIn, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> ChannelOut:
    defaults = _check(body.theme, body.style, body.defaults)
    ch = Channel(user_id=user.id, name=body.name.strip(), theme=body.theme, style=body.style, notes=body.notes,
                 defaults=defaults or {})
    session.add(ch)
    await session.commit()
    return (await _out(session, [ch]))[0]


@router.get("/channels/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                      user: User = Depends(current_user)) -> ChannelOut:
    return (await _out(session, [await _channel(session, user, channel_id)]))[0]


@router.patch("/channels/{channel_id}", response_model=ChannelOut, responses={422: {"description": "Bad settings"}})
async def update_channel(channel_id: uuid.UUID, body: ChannelPatch, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> ChannelOut:
    ch = await _channel(session, user, channel_id)
    defaults = _check(body.theme, body.style, body.defaults)
    for field in ("name", "theme", "style", "notes"):
        if (value := getattr(body, field)) is not None:
            setattr(ch, field, value.strip() if field == "name" else value)
    if defaults is not None:
        ch.defaults = defaults
    await session.commit()
    return (await _out(session, [ch]))[0]


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                         user: User = Depends(current_user)) -> None:
    """Hide the channel. Its episodes stay in Runs and the library."""
    ch = await _channel(session, user, channel_id)
    ch.deleted_at = datetime.now(UTC)
    await session.commit()
