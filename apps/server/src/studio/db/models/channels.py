from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, TimestampMixin


class Channel(IdMixin, TimestampMixin, Base):
    """A Faceless Studio channel: a series of episodes sharing a theme, style, voice and recurring characters.

    `defaults` holds the episode settings (format, aspect, voice, models, music, captions...), in the faceless
    recipe's input shape. A new episode starts from them; each episode run records its own copy.
    """

    __tablename__ = "channels"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    theme: Mapped[str] = mapped_column(String(40))
    style: Mapped[str] = mapped_column(String(60))
    notes: Mapped[str] = mapped_column(Text, default="")  # standing guidance for the writer
    defaults: Mapped[dict[str, Any]] = mapped_column(default=dict)
    deleted_at: Mapped[datetime | None]
