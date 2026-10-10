"""mode switch

The Settings switch between mock and real generation (and Claude): the user's choice, capped by .env.

Revision ID: 8f3a4b5c6d7e
Revises: 7e2f3a4b5c6d
Create Date: 2026-10-10 16:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a4b5c6d7e"
down_revision: str | Sequence[str] | None = "7e2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("budget_policies", sa.Column("provider_mode", sa.String(length=10), nullable=True))
    op.add_column("budget_policies", sa.Column("llm_mode", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("budget_policies", "llm_mode")
    op.drop_column("budget_policies", "provider_mode")
