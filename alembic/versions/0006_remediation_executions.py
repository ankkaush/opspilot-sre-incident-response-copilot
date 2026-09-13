"""remediation execution idempotency guard (v0.5 Phase 2)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remediation_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_remediation_executions_idempotency_key",
        "remediation_executions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_remediation_executions_idempotency_key", table_name="remediation_executions")
    op.drop_table("remediation_executions")
