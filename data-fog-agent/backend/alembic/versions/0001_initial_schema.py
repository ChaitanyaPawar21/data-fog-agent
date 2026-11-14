"""Initial schema: all tables from the spec, plus row-level security per org.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536

# Every table except orgs. The policy below hides rows of other orgs from the app.
ORG_TABLES = [
    "users",
    "groups",
    "user_groups",
    "sources",
    "assets",
    "columns",
    "asset_permissions",
    "knowledge_snippets",
    "proposals",
    "alerts",
    "audit_log",
    "sync_runs",
]


def text_choice(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def timestamp(name: str, **kwargs) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def org_id_column(index: bool = False) -> sa.Column:
    return sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False, index=index)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "orgs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        timestamp("created_at", server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", text_choice("admin", "member", name="user_role"), nullable=False),
        timestamp("created_at", server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.UniqueConstraint("org_id", "name"),
    )

    op.create_table(
        "user_groups",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id"), primary_key=True),
        org_id_column(),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column(
            "type",
            text_choice("postgres", "files", "slack_export", name="source_type"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("encrypted_config", sa.Text, nullable=False),
        sa.Column("sync_cursor", sa.dialects.postgresql.JSONB),
        timestamp("last_synced_at"),
        sa.Column("status", sa.String(20), server_default="idle", nullable=False),
        sa.Column("last_error", sa.Text),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=False, index=True),
        sa.Column(
            "type", text_choice("table", "file", "channel", name="asset_type"), nullable=False
        ),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "description_status",
            text_choice("none", "proposed", "approved", name="asset_description_status"),
            server_default="none",
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("row_count", sa.BigInteger),
        sa.Column("size_bytes", sa.BigInteger),
        timestamp("last_modified_at"),
        timestamp("last_seen_at"),
        sa.Column("archived", sa.Boolean, server_default="false", nullable=False),
        sa.Column("trust_score", sa.Integer),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM)),
        sa.Column("content_hash", sa.String(64)),
    )

    op.create_table(
        "columns",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), nullable=False, index=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("data_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "description_status",
            text_choice("none", "proposed", "approved", name="column_description_status"),
            server_default="none",
            nullable=False,
        ),
        sa.Column("pii_type", sa.String(20)),
    )

    op.create_table(
        "asset_permissions",
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), primary_key=True),
        sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id"), primary_key=True),
        org_id_column(),
    )

    op.create_table(
        "knowledge_snippets",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("author", sa.String(200)),
        timestamp("posted_at"),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM)),
    )

    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column("type", text_choice("merge", "doc", name="proposal_type"), nullable=False),
        sa.Column(
            "status",
            text_choice(
                "pending", "approved", "rejected", "done", "failed", name="proposal_status"
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        timestamp("created_at", server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("users.id")),
        timestamp("reviewed_at"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(index=True),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("assets.id"), nullable=False),
        sa.Column(
            "type",
            text_choice("stale", "schema_change", "pii_found", name="alert_type"),
            nullable=False,
        ),
        sa.Column("message", sa.Text, nullable=False),
        timestamp("created_at", server_default=sa.func.now(), nullable=False),
        timestamp("resolved_at"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        org_id_column(index=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column(
            "actor",
            text_choice("user", "agent", "mcp", "worker", name="audit_actor"),
            nullable=False,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.Text),
        sa.Column("details", sa.dialects.postgresql.JSONB),
        timestamp("created_at", server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        org_id_column(),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=False, index=True),
        timestamp("started_at", server_default=sa.func.now(), nullable=False),
        timestamp("finished_at"),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("assets_seen", sa.Integer, server_default="0", nullable=False),
        sa.Column("error", sa.Text),
    )

    for table in ORG_TABLES:
        # FORCE makes the policy apply to the table owner too, which is the role the app uses.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # NULLIF is needed because a pooled connection keeps the setting as '' after the
        # transaction that set it ends, and ''::int would raise an error.
        op.execute(f"""
            CREATE POLICY org_isolation ON {table}
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR org_id = NULLIF(current_setting('app.org_id', true), '')::int
            )
        """)


def downgrade() -> None:
    for table in reversed(ORG_TABLES):
        op.drop_table(table)
    op.drop_table("orgs")
