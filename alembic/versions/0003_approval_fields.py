"""incident approval fields (v0.2 Phase 3)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("pending_approval", postgresql.JSONB(), nullable=True))
    op.add_column(
        "incidents", sa.Column("awaiting_since", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("incidents", "awaiting_since")
    op.drop_column("incidents", "pending_approval")
