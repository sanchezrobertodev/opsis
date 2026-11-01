from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base


class ProviderKey(Base):
    """A provider key pasted in Settings, encrypted (studio/keys.py). It wins over the same setting in .env."""

    __tablename__ = "provider_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    setting: Mapped[str] = mapped_column(String(40), primary_key=True)  # the Settings attribute, e.g. fal_key
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
