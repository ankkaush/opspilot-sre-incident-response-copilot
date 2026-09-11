"""SQLAlchemy models for the synthetic SRE environment.

Every table here is populated only by the deterministic seed generator
(opspilot.seed) — there is no user-facing write path onto this data in v0.1.
"""

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opspilot.db import Base


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="service")
    runbooks: Mapped[list["Runbook"]] = relationship(back_populates="service")


class Scenario(Base):
    """One deterministic, reproducible incident scenario.

    `ground_truth` is the structured block every scenario carries from v0.1
    onward: what actually caused the incident, what a correct investigation
    should find, and what the correct outcome is. Nothing reads this field
    until the v0.3 evaluation harness — it exists now because writing it
    after the fact, once dozens of scenarios exist, is expensive.
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    injected_cause: Mapped[str] = mapped_column(Text)
    incident_started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    # {
    #   "expected_evidence": ["deployment", "metrics:error_rate", "logs"],
    #   "expected_diagnosis": "...",
    #   "expected_action": "rollback_deployment",
    #   "expected_policy_verdict": "REQUIRE_APPROVAL",  # EXECUTE|REQUIRE_APPROVAL|BLOCK|ESCALATE
    #   "notes": "..."
    # }
    ground_truth: Mapped[dict] = mapped_column(JSONB)

    service: Mapped["Service"] = relationship(back_populates="scenarios")
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="scenario")
    metrics: Mapped[list["MetricPoint"]] = relationship(back_populates="scenario")
    logs: Mapped[list["LogEntry"]] = relationship(back_populates="scenario")
    dependency_statuses: Mapped[list["DependencyStatus"]] = relationship(back_populates="scenario")


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    version: Mapped[str] = mapped_column(String(32))
    deployed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    diff_summary: Mapped[str] = mapped_column(Text)

    scenario: Mapped["Scenario"] = relationship(back_populates="deployments")


class MetricPoint(Base):
    __tablename__ = "metric_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    metric_name: Mapped[str] = mapped_column(String(32))  # error_rate|latency_ms|cpu_pct|mem_pct
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float]

    scenario: Mapped["Scenario"] = relationship(back_populates="metrics")


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[str] = mapped_column(String(16))  # info | warn | error
    message: Mapped[str] = mapped_column(Text)

    scenario: Mapped["Scenario"] = relationship(back_populates="logs")


class DependencyStatus(Base):
    __tablename__ = "dependency_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), index=True)
    dependency_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # healthy | degraded | down
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    scenario: Mapped["Scenario"] = relationship(back_populates="dependency_statuses")


class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    symptom_keyword: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)

    service: Mapped["Service"] = relationship(back_populates="runbooks")


class Incident(Base):
    """One real, user-created investigation run against a seeded scenario.

    A Scenario is the reusable, deterministic *fixture*; an Incident is one
    attempt at investigating it — created by a human, run through the agent
    loop, and left with a permanent record. Re-running is deliberately not
    supported (see routers/incidents.py) — an Incident is a single, honest
    attempt, not a scratchpad.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), index=True)
    # open -> running -> diagnosed | incomplete_step_ceiling | incomplete_cost_ceiling
    status: Mapped[str] = mapped_column(String(32), default="open")
    diagnosis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    steps_used: Mapped[int | None] = mapped_column(nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scenario: Mapped["Scenario"] = relationship()
    audit_entries: Mapped[list["AuditLogEntry"]] = relationship(
        back_populates="incident", order_by="AuditLogEntry.id"
    )


class AuditLogEntry(Base):
    """Append-only: one row per tool call (including submit_diagnosis) made
    during an Incident's investigation. Nothing ever updates or deletes a
    row here — this table is the audit trail, not application state."""

    __tablename__ = "audit_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    step: Mapped[int]
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    incident: Mapped["Incident"] = relationship(back_populates="audit_entries")
