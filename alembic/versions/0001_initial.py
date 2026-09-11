"""initial schema — synthetic SRE environment

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_unique_constraint("uq_services_name", "services", ["name"])
    op.create_index("ix_services_name", "services", ["name"])

    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("injected_cause", sa.Text(), nullable=False),
        sa.Column("incident_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ground_truth", postgresql.JSONB(), nullable=False),
    )
    op.create_unique_constraint("uq_scenarios_key", "scenarios", ["key"])
    op.create_index("ix_scenarios_key", "scenarios", ["key"])

    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("diff_summary", sa.Text(), nullable=False),
    )
    op.create_index("ix_deployments_scenario_id", "deployments", ["scenario_id"])

    op.create_table(
        "metric_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("metric_name", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )
    op.create_index("ix_metric_points_scenario_id", "metric_points", ["scenario_id"])

    op.create_table(
        "log_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_log_entries_scenario_id", "log_entries", ["scenario_id"])

    op.create_table(
        "dependency_statuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("dependency_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dependency_statuses_scenario_id", "dependency_statuses", ["scenario_id"])

    op.create_table(
        "runbooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("symptom_keyword", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_runbooks_service_id", "runbooks", ["service_id"])


def downgrade() -> None:
    op.drop_table("runbooks")
    op.drop_table("dependency_statuses")
    op.drop_table("log_entries")
    op.drop_table("metric_points")
    op.drop_table("deployments")
    op.drop_table("scenarios")
    op.drop_table("services")
