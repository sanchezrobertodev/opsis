from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, TimestampMixin
from studio.db.models._types import str_enum


class ElementKind(StrEnum):
    character = "character"
    location = "location"
    prop = "prop"
    style = "style"
    voice = "voice"


class Element(IdMixin, TimestampMixin, Base):
    """A reusable character / location / prop / style. `@slug` in a prompt attaches its reference images."""

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
    cover_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id"))
    deleted_at: Mapped[datetime | None]


class ElementAsset(Base):
    __tablename__ = "element_assets"

    element_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elements.id"), primary_key=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    view: Mapped[str] = mapped_column(String(24), default="front")  # front, three_quarter, side, back, face, full_body, sheet, wide, detail
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
