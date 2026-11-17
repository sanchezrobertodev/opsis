"""element profile

A character's profile (personality, backstory, catchphrases, how they talk), which the writers use where they write
the character's lines.

Revision ID: f0a1b2c3d4e5
Revises: ef9a0b1c2d3e
Create Date: 2026-11-17 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "ef9a0b1c2d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "elements",
        sa.Column(
            "profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("elements", "profile")
