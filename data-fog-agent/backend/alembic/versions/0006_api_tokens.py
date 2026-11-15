"""Personal API tokens for MCP clients.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        # SHA-256 of the token. The token itself is shown once and never stored.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY org_isolation ON api_tokens
        USING (
            current_setting('app.bypass_rls', true) = 'on'
            OR org_id = NULLIF(current_setting('app.org_id', true), '')::int
        )
    """)


def downgrade() -> None:
    op.drop_table("api_tokens")
