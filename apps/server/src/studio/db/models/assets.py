from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from studio.db.base import Base, IdMixin, TimestampMixin
from studio.db.models._types import str_enum
from studio.domain.enums import AssetKind, AssetSource, AssetStatus


class Asset(IdMixin, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("generation_id", "output_index"),
        Index("ix_assets_user_kind_created", "user_id", "kind", "created_at"),
        Index("ix_assets_tags", "tags", postgresql_using="gin"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[AssetKind] = mapped_column(str_enum(AssetKind))
    status: Mapped[AssetStatus] = mapped_column(str_enum(AssetStatus), default=AssetStatus.processing)
    source: Mapped[AssetSource] = mapped_column(str_enum(AssetSource))
    mime: Mapped[str] = mapped_column(String(127))
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None]
    has_audio: Mapped[bool | None] = mapped_column(Boolean)
    codec: Mapped[str | None] = mapped_column(String(32))

    # {"thumb": key, "poster": key, "filmstrip": key, "proxy": key, "waveform": key}
    derivatives: Mapped[dict[str, Any]] = mapped_column(default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    # Provider-side handles needed for follow-up ops, e.g. {"google": {"file_uri": ..., "expires_at": ...}}
    provider_refs: Mapped[dict[str, Any]] = mapped_column(default=dict)

    generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generations.id"), index=True)
    output_index: Mapped[int | None] = mapped_column(Integer)
    project_id: Mapped[uuid.UUID | None]
    title: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None]


class AssetLineage(Base):
    """Edge: `child` was produced from `parent` in the given role (start_image, extend_of, ...)."""

    __tablename__ = "asset_lineage"

    child_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    parent_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"), primary_key=True, index=True)
    relation: Mapped[str] = mapped_column(String(40), primary_key=True)
    generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generations.id"))
    pipeline_run_id: Mapped[uuid.UUID | None]


class AssetProviderUpload(IdMixin, TimestampMixin, Base):
    """Cache of inputs already staged at a provider, so the same file isn't re-uploaded."""

    __tablename__ = "asset_provider_uploads"
    __table_args__ = (UniqueConstraint("asset_id", "provider"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    provider: Mapped[str] = mapped_column(String(40))
    remote_ref: Mapped[str] = mapped_column(String(2048))
    expires_at: Mapped[datetime | None]
