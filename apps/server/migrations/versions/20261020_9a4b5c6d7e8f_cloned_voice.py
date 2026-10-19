"""cloned voice

The id of a voice cloned on a voice service, set only by the clone endpoint, so deleting the element removes exactly
the voice this account made.

Revision ID: 9a4b5c6d7e8f
Revises: 8f3a4b5c6d7e
Create Date: 2026-10-20 00:30:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a4b5c6d7e8f"
down_revision: str | Sequence[str] | None = "8f3a4b5c6d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("elements", sa.Column("cloned_voice_id", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("elements", "cloned_voice_id")
