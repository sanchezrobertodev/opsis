"""procrastinate queue schema + seeded owner and budget policy

Revision ID: b1a2c3d4e5f6
Revises: d4c0e4baceef
Create Date: 2026-10-06 08:00:00
"""
from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager
from sqlalchemy.util import await_

from studio.config import DEFAULT_OWNER_ID

revision: str = "b1a2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d4c0e4baceef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # procrastinate's full schema (tables, functions, triggers) for its current version.
    # Future procrastinate upgrades ship SQL migrations; add them as new Alembic revisions.
    # asyncpg can't run multi-statement SQL as a prepared statement; use the simple query
    # protocol on the raw driver connection (same transaction as the rest of the migration).
    raw = op.get_bind().connection.dbapi_connection
    await_(raw.driver_connection.execute(SchemaManager.get_schema()))
    op.execute(
        f"INSERT INTO users (id, display_name, role, settings) "
        f"VALUES ('{DEFAULT_OWNER_ID}', 'Owner', 'owner', '{{}}'::jsonb) ON CONFLICT DO NOTHING"
    )
    op.execute(
        f"INSERT INTO budget_policies (user_id, live_enabled, daily_cap_usd, monthly_cap_usd, "
        f"confirm_over_usd, default_run_cap_usd) VALUES ('{DEFAULT_OWNER_ID}', true, 20, 200, 2, 10) "
        f"ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM budget_policies WHERE user_id = '{DEFAULT_OWNER_ID}'")
    op.execute(f"DELETE FROM users WHERE id = '{DEFAULT_OWNER_ID}'")
    op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
