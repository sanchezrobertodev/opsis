"""looks and casts

A character's looks (`@maya:winter`: an outfit or another picture style), whose pictures are the links with that
`look`, and casts: saved groups of characters (`@crew` attaches its members' pictures).

Revision ID: ef9a0b1c2d3e
Revises: de8f9a0b1c2d
Create Date: 2026-11-17 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ef9a0b1c2d3e"
down_revision: str | Sequence[str] | None = "de8f9a0b1c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("character", "location", "prop", "style", "voice")
NEW = (*OLD, "cast")


def _check(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_elements_elementkind"), "elements", type_="check")
    op.create_check_constraint(
        op.f("ck_elements_elementkind"), "elements", "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"
    )


def upgrade() -> None:
    op.add_column("element_assets", sa.Column("look", sa.String(length=24), nullable=True))
    op.add_column(
        "elements",
        sa.Column(
            "looks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    _check(NEW)
    op.create_table(
        "element_members",
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["elements.id"],
            name=op.f("fk_element_members_group_id_elements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["elements.id"],
            name=op.f("fk_element_members_member_id_elements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_element_members_user_id_users")),
        sa.PrimaryKeyConstraint("group_id", "member_id", name=op.f("pk_element_members")),
    )
    op.create_index(op.f("ix_element_members_member_id"), "element_members", ["member_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_element_members_member_id"), table_name="element_members")
    op.drop_table("element_members")
    op.execute("DELETE FROM element_assets WHERE element_id IN (SELECT id FROM elements WHERE kind = 'cast')")
    op.execute("DELETE FROM elements WHERE kind = 'cast'")
    _check(OLD)
    # a look's pictures would become main ones and the grid sheets plain pictures, which every mention attaches;
    # the assets stay in the library
    op.execute("DELETE FROM element_assets WHERE look IS NOT NULL OR view IN ('expressions', 'poses')")
    op.drop_column("elements", "looks")
    op.drop_column("element_assets", "look")
