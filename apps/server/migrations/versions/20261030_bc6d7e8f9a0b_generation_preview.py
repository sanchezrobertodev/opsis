"""generation preview_key

The latest preview frame a provider sent while the take renders (local ComfyUI's sampler previews).

Revision ID: bc6d7e8f9a0b
Revises: ab5c6d7e8f9a
Create Date: 2026-10-30 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bc6d7e8f9a0b"
down_revision: str | Sequence[str] | None = "ab5c6d7e8f9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("preview_key", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("generations", "preview_key")
