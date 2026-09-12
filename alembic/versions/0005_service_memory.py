"""service memory (v0.4 Phase 1)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-13
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("source_incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("symptom_pattern", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("fix_applied", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_service_memory_service_id", "service_memory", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_service_memory_service_id", table_name="service_memory")
    op.drop_table("service_memory")
