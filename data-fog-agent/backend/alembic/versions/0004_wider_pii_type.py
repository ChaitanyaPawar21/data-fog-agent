"""columns.pii_type holds every kind found, like "AADHAAR,PHONE".

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("columns", "pii_type", type_=sa.String(100))


def downgrade() -> None:
    op.alter_column("columns", "pii_type", type_=sa.String(20))
