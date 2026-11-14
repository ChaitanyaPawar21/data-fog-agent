"""Chat conversations, one per user thread.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        # json, not jsonb: jsonb reorders keys, and the model's history is sent back as stored.
        sa.Column("messages", sa.JSON, nullable=False),
        sa.Column("turns", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON conversations
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR org_id = NULLIF(current_setting('app.org_id', true), '')::int
        )
    """)


def downgrade() -> None:
    op.drop_table("conversations")
