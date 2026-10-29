"""generation deleted_at

Takes removed from history are kept as rows (their attempts carry the spending records) and hidden by this mark.

Revision ID: ab5c6d7e8f9a
Revises: 9a4b5c6d7e8f
Create Date: 2026-10-29 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab5c6d7e8f9a"
down_revision: str | Sequence[str] | None = "9a4b5c6d7e8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("generations", "deleted_at")
