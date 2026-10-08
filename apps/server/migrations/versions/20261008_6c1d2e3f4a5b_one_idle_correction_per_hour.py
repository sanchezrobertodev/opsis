"""one idle correction per endpoint hour

Bill corrections for time no take paid for (a RunPod worker idling after its job) have no attempt, so the
(attempt, entry type) constraint can't stop two overlapping reconcile runs from booking the same hour twice.

Revision ID: 6c1d2e3f4a5b
Revises: 5bd9e52224a0
Create Date: 2026-10-08 12:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = "6c1d2e3f4a5b"
down_revision: str | Sequence[str] | None = "5bd9e52224a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX uq_spend_ledger_idle_bucket ON spend_ledger (provider, (basis->>'bucket'))
        WHERE entry_type = 'adjustment' AND generation_attempt_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_spend_ledger_idle_bucket")
