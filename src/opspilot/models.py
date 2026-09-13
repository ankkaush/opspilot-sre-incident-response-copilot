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
    # open -> running -> (awaiting_approval -> running again ->) diagnosed |
    #   incomplete_step_ceiling | incomplete_cost_ceiling | incomplete_provider_error
    status: Mapped[str] = mapped_column(String(32), default="open")
    diagnosis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    steps_used: Mapped[int | None] = mapped_column(nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # v0.2 Phase 3 — human-in-the-loop. pending_approval is the interrupt
    # payload while status == "awaiting_approval" (what's being asked and
    # why); awaiting_since anchors the SLA-timeout check. Both cleared once
    # an approval decision resolves the pause.
    pending_approval: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    awaiting_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # v0.3 Phase 3 — the Langfuse trace id for the run/resume call that most
    # recently touched this incident, so the dashboard can link straight to
    # the full trace. Overwritten on resume (a paused incident's run and its
    # eventual resume are two separate traces — see graph.py); the earlier
    # trace id isn't lost, it's just not the one linked from here anymore.
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    scenario: Mapped["Scenario"] = relationship()
    audit_entries: Mapped[list["AuditLogEntry"]] = relationship(
        back_populates="incident", order_by="AuditLogEntry.id"
    )


class ServiceMemory(Base):
    """Confirmed, structured facts about a service's past incidents —
    long-term, cross-incident memory (v0.4 Phase 1), distinct from the
    within-run state LangGraph already carries.

    Written only at incident close, only from the structured diagnosis
    object, and only when the write-policy gate in `opspilot.memory`
    (confidence floor, never for an 'escalate' recommendation) passes —
    never directly from raw model chatter. `occurrence_count` starts at 1
    and grows as `opspilot.memory.consolidate_service_memory` merges
    near-duplicate rows (same service, same symptom pattern, same fix) into
    one, rather than letting confirmations of the same pattern pile up.
    """

    __tablename__ = "service_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    source_incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    symptom_pattern: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    fix_applied: Mapped[str] = mapped_column(String(32))
    # executed | approved_and_executed | denied | blocked — derived from the
    # policy verdict and (if any) the approval decision, never from the
    # model's own account of what happened. See opspilot.memory._derive_outcome.
    outcome: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float]
    occurrence_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    service: Mapped["Service"] = relationship()


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


class RemediationExecution(Base):
    """The idempotency guard for remediation actions (v0.5 Phase 2).

    LangGraph re-enters a node function from the top when resuming past an
    `interrupt()` call (see `opspilot.agent.nodes.evaluate_policy`'s own
    docstring) — a crash after the human's decision is known but before
    that node's checkpoint is durably written means a later resume replays
    the *already-answered* interrupt and reaches the remediation call
    again. One row here, keyed by `idempotency_key`, is what makes a second
    attempt at the identical (thread, action, target, params) tuple return
    the first attempt's recorded result instead of calling the remediation
    tool a second time — see `opspilot.agent.idempotency.
    execute_idempotently`, the only writer of this table.

    Written via its own immediately-committed session, deliberately not
    the long-lived per-investigation one: the guarantee only holds if this
    row survives a crash that happens before that session's own eventual
    commit ever runs.
    """

    __tablename__ = "remediation_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    thread_id: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
