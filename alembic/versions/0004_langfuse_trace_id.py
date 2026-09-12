"""incident langfuse trace id (v0.3 Phase 3)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("langfuse_trace_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "langfuse_trace_id")
