from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, TimestampMixin
from studio.db.models._types import str_enum


class ElementKind(StrEnum):
    character = "character"
    location = "location"
    prop = "prop"
    style = "style"
    voice = "voice"
    cast = "cast"  # a saved group of characters (`@crew`), not a film's cast in Cinema Studio


class Element(IdMixin, TimestampMixin, Base):
    """A reusable character / location / prop / style, or a cast (group) of characters. `@slug` in a prompt attaches
    its reference images (a cast's: its members'), `@slug:look` a look's first."""

    __tablename__ = "elements"
    __table_args__ = (UniqueConstraint("user_id", "slug"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[ElementKind] = mapped_column(str_enum(ElementKind))
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")  # canonical identity text, kept short
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)  # trait builder choices
    voice: Mapped[dict[str, Any]] = mapped_column(default=dict)
    lora: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # a character's looks, `@slug:look`: {look: {name, description}}; their pictures are links with that `look`
    looks: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    cover_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    # set only when the clone endpoint made this voice on ElevenLabs: deleting the element removes exactly that voice
    cloned_voice_id: Mapped[str | None] = mapped_column(String(80))
    deleted_at: Mapped[datetime | None]


class ElementAsset(Base):
    __tablename__ = "element_assets"

    element_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id"), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    view: Mapped[str] = mapped_column(String(24), default="front")  # routers/elements.py:View (front, side, sheet, the expressions and poses grids...)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    look: Mapped[str | None] = mapped_column(String(24))  # one of the element's looks; NULL: a main picture


class ElementMember(Base):
    """A character in a cast (a saved group), in order."""

    __tablename__ = "element_members"

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True,
                                                 index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    sort: Mapped[int] = mapped_column(Integer, default=0)
