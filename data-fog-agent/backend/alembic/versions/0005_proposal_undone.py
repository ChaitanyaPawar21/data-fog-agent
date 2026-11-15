"""Merge proposals can be undone.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "'pending', 'approved', 'rejected', 'done', 'failed'"


def upgrade() -> None:
    op.drop_constraint("proposal_status", "proposals", type_="check")
    op.create_check_constraint("proposal_status", "proposals", f"status IN ({OLD}, 'undone')")


def downgrade() -> None:
    op.drop_constraint("proposal_status", "proposals", type_="check")
    op.create_check_constraint("proposal_status", "proposals", f"status IN ({OLD})")
