"""archive asset kind

A zip of pictures a LoRA trainer learns from (the lora_train recipe's dataset) is stored as an asset of its own.

Revision ID: 7e2f3a4b5c6d
Revises: 6c1d2e3f4a5b
Create Date: 2026-10-08 20:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = "7e2f3a4b5c6d"
down_revision: str | Sequence[str] | None = "6c1d2e3f4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("video", "image", "audio", "mask", "mask_video", "depth", "pose", "model3d", "splat", "panorama", "lora",
       "subtitle", "document")
NEW = (*OLD, "archive")


def _check(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_assets_assetkind"), "assets", type_="check")
    op.create_check_constraint(op.f("ck_assets_assetkind"), "assets",
                               "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")")


def upgrade() -> None:
    _check(NEW)


def downgrade() -> None:
    op.execute("DELETE FROM asset_lineage WHERE child_asset_id IN (SELECT id FROM assets WHERE kind = 'archive') "
               "OR parent_asset_id IN (SELECT id FROM assets WHERE kind = 'archive')")
    op.execute("DELETE FROM assets WHERE kind = 'archive'")
    _check(OLD)
