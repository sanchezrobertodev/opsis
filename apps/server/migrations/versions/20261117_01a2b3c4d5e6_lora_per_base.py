"""lora per base

A character keeps a trained identity per LoRA base (`{base: identity}`: Wan 2.2's and FLUX 3 Klein's side by side),
instead of one.

Revision ID: 01a2b3c4d5e6
Revises: f0a1b2c3d4e5
Create Date: 2026-11-17 21:00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "01a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # an identity without a base never loaded anywhere; it stays as it is (a NULL key would fail the update)
    op.execute(
        "UPDATE elements SET lora = jsonb_build_object(lora->>'base', lora) "
        "WHERE lora ? 'base' AND lora->>'base' IS NOT NULL"
    )


def downgrade() -> None:
    # one identity per character again: the one trained last
    op.execute(
        "UPDATE elements SET lora = (SELECT value FROM jsonb_each(lora) "
        "ORDER BY value->>'trained_at' DESC NULLS LAST LIMIT 1) "
        "WHERE jsonb_typeof(lora) = 'object' AND lora <> '{}'::jsonb AND NOT lora ? 'base' AND NOT lora ? 'low'"
    )
