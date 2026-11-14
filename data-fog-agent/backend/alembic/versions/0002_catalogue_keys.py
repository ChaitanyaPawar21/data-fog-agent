"""Catalogue keys: unique asset path per source, unique column name per asset,
and link each Slack snippet to its channel asset.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("assets_source_id_path_key", "assets", ["source_id", "path"])
    op.create_unique_constraint("columns_asset_id_name_key", "columns", ["asset_id", "name"])
    # No snippets exist before this phase, so the column can be NOT NULL straight away.
    op.add_column(
        "knowledge_snippets",
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), nullable=False),
    )
    op.create_index("ix_knowledge_snippets_asset_id", "knowledge_snippets", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_snippets_asset_id", "knowledge_snippets")
    op.drop_column("knowledge_snippets", "asset_id")
    op.drop_constraint("columns_asset_id_name_key", "columns")
    op.drop_constraint("assets_source_id_path_key", "assets")
