"""claude models

Which Claude model writes scripts and plans, and which helps with prompts: the Settings choice, else .env's.

Revision ID: de8f9a0b1c2d
Revises: cd7e8f9a0b1c
Create Date: 2026-11-01 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "de8f9a0b1c2d"
down_revision: str | Sequence[str] | None = "cd7e8f9a0b1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("budget_policies", sa.Column("llm_smart_model", sa.String(length=40), nullable=True))
    op.add_column("budget_policies", sa.Column("llm_fast_model", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("budget_policies", "llm_fast_model")
    op.drop_column("budget_policies", "llm_smart_model")
