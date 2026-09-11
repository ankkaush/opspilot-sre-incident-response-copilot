"""incidents and audit log

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("diagnosis", postgresql.JSONB(), nullable=True),
        sa.Column("steps_used", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_incidents_scenario_id", "incidents", ["scenario_id"])

    op.create_table(
        "audit_log_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_entries_incident_id", "audit_log_entries", ["incident_id"])


def downgrade() -> None:
    op.drop_table("audit_log_entries")
    op.drop_table("incidents")
