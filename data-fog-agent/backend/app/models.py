from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# gemini-embedding-2 asked for 1536 dimensions (see llm.py).
# Changing it needs a migration and re-embedding.
EMBEDDING_DIM = 1536


class Base(DeclarativeBase):
    pass


def created_at_column():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def choice(*values: str, name: str):
    # Stored as text with a CHECK constraint, so adding a value later is a small migration.
    return Enum(*values, name=name, native_enum=False, create_constraint=True)


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_column()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(choice("admin", "member", name="user_role"))
    created_at: Mapped[datetime] = created_at_column()


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    name: Mapped[str] = mapped_column(String(200))


class UserGroup(Base):
    __tablename__ = "user_groups"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    type: Mapped[str] = mapped_column(
        choice("postgres", "files", "slack_export", name="source_type")
    )
    name: Mapped[str] = mapped_column(String(200))
    encrypted_config: Mapped[str] = mapped_column(Text)
    sync_cursor: Mapped[dict | None] = mapped_column(JSONB)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="idle")
    last_error: Mapped[str | None] = mapped_column(Text)


class Asset(Base):
    __tablename__ = "assets"
    # Sync matches what a connector reports to existing rows by path.
    __table_args__ = (UniqueConstraint("source_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    type: Mapped[str] = mapped_column(choice("table", "file", "channel", name="asset_type"))
    name: Mapped[str] = mapped_column(String(500))
    path: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    description_status: Mapped[str] = mapped_column(
        choice("none", "proposed", "approved", name="asset_description_status"),
        server_default="none",
    )
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived: Mapped[bool] = mapped_column(server_default="false")
    trust_score: Mapped[int | None]
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))


class AssetColumn(Base):
    __tablename__ = "columns"
    __table_args__ = (UniqueConstraint("asset_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    data_type: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    description_status: Mapped[str] = mapped_column(
        choice("none", "proposed", "approved", name="column_description_status"),
        server_default="none",
    )
    # Every kind found, comma-separated, like "AADHAAR,PHONE".
    pii_type: Mapped[str | None] = mapped_column(String(100))


class AssetPermission(Base):
    """An asset with no rows here is visible to admins only."""

    __tablename__ = "asset_permissions"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))


class KnowledgeSnippet(Base):
    """A message from a Slack export."""

    __tablename__ = "knowledge_snippets"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    # The channel it was posted in. Snippets are visible to whoever can see that channel.
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(200))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    type: Mapped[str] = mapped_column(choice("merge", "doc", name="proposal_type"))
    status: Mapped[str] = mapped_column(
        choice(
            "pending", "approved", "rejected", "done", "failed", "undone", name="proposal_status"
        ),
        server_default="pending",
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    type: Mapped[str] = mapped_column(
        choice("stale", "schema_change", "pii_found", name="alert_type")
    )
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(choice("user", "agent", "mcp", "worker", name="audit_actor"))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    started_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="running")
    assets_seen: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class ApiToken(Base):
    """A personal token for MCP clients. It acts as the user who made it."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # SHA-256 of the token. The token itself is shown once and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    # What the model sees, in Messages API format. Stored as-is: see agent.answer.
    messages: Mapped[list] = mapped_column(JSON)
    # What the chat page shows: [{"role", "text", "sources", "table"}].
    turns: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
