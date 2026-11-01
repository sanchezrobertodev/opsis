"""provider keys

Provider keys pasted in Settings, encrypted with the studio's key secret (studio/keys.py).

Revision ID: cd7e8f9a0b1c
Revises: bc6d7e8f9a0b
Create Date: 2026-11-01 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cd7e8f9a0b1c"
down_revision: str | Sequence[str] | None = "bc6d7e8f9a0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_keys",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("setting", sa.String(length=40), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_provider_keys_user_id_users")),
        sa.PrimaryKeyConstraint("user_id", "setting", name=op.f("pk_provider_keys")),
    )


def downgrade() -> None:
    op.drop_table("provider_keys")
