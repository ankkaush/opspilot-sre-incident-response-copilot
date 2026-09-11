"""The agent's tool layer — five read-only tools over the synthetic environment.

Every tool is bound to one `Scenario` via `ToolContext`, set once when a
`investigate()` run starts. The model is never given a scenario or service id
to pass around — it can only ask about metrics/logs/deployments/dependencies/
runbooks for *this* incident. That boundary is enforced here, in code, not by
the model choosing to behave — the same "deterministic code controls scope"
principle the policy engine will apply to remediation in v0.2.

Nothing here has a side effect. All five tools are pure reads, so there is no
idempotency concern to design for — that only becomes real once v0.2 adds
remediation tools that change state.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from opspilot.models import DependencyStatus, Deployment, LogEntry, MetricPoint, Runbook, Scenario


@dataclass(frozen=True)
class ToolContext:
    session: Session
    scenario: Scenario


class GetMetricsArgs(BaseModel):
    metric_name: str | None = Field(
        default=None,
        description="Filter to one metric: error_rate, latency_ms, cpu_pct, or mem_pct. Omit for all.",
    )
    since_minutes: int = Field(
        default=60, ge=1, le=10080, description="Look back this many minutes before incident start."
    )


class GetLogsArgs(BaseModel):
    level: str | None = Field(default=None, description="Filter to one level: info, warn, or error.")
    since_minutes: int = Field(default=60, ge=1, le=10080)
    limit: int = Field(default=20, ge=1, le=100)


class GetRecentDeploymentsArgs(BaseModel):
    since_minutes: int = Field(
        default=1440, ge=1, le=10080, description="Look back this many minutes before incident start."
    )


class GetDependencyStatusArgs(BaseModel):
    pass


class GetRunbookArgs(BaseModel):
    symptom_keyword: str = Field(
        min_length=2, description="A keyword describing the symptom, e.g. 'connection pool exhausted'."
    )


def _window(scenario: Scenario, since_minutes: int) -> tuple[dt.datetime, dt.datetime]:
    """Every tool's time window is anchored to the scenario's own incident
    start, never to wall-clock 'now' — these are historical synthetic
    incidents, some of them years in the past by the time this runs."""
    start = scenario.incident_started_at - dt.timedelta(minutes=since_minutes)
    end = scenario.incident_started_at + dt.timedelta(minutes=15)
    return start, end


def get_metrics(ctx: ToolContext, args: GetMetricsArgs) -> list[dict]:
    start, end = _window(ctx.scenario, args.since_minutes)
    query = (
        ctx.session.query(MetricPoint)
        .filter(MetricPoint.scenario_id == ctx.scenario.id)
        .filter(MetricPoint.timestamp >= start, MetricPoint.timestamp <= end)
    )
    if args.metric_name:
        query = query.filter(MetricPoint.metric_name == args.metric_name)
    rows = query.order_by(MetricPoint.timestamp).all()
    return [
        {"metric_name": r.metric_name, "timestamp": r.timestamp.isoformat(), "value": r.value}
        for r in rows
    ]


def get_logs(ctx: ToolContext, args: GetLogsArgs) -> list[dict]:
    start, end = _window(ctx.scenario, args.since_minutes)
    query = (
        ctx.session.query(LogEntry)
        .filter(LogEntry.scenario_id == ctx.scenario.id)
        .filter(LogEntry.timestamp >= start, LogEntry.timestamp <= end)
    )
    if args.level:
        query = query.filter(LogEntry.level == args.level)
    rows = query.order_by(LogEntry.timestamp).limit(args.limit).all()
    return [
        {"timestamp": r.timestamp.isoformat(), "level": r.level, "message": r.message} for r in rows
    ]


def get_recent_deployments(ctx: ToolContext, args: GetRecentDeploymentsArgs) -> list[dict]:
    start, end = _window(ctx.scenario, args.since_minutes)
    rows = (
        ctx.session.query(Deployment)
        .filter(Deployment.scenario_id == ctx.scenario.id)
        .filter(Deployment.deployed_at >= start, Deployment.deployed_at <= end)
        .order_by(Deployment.deployed_at)
        .all()
    )
    return [
        {
            "version": r.version,
            "deployed_at": r.deployed_at.isoformat(),
            "diff_summary": r.diff_summary,
        }
        for r in rows
    ]


def get_dependency_status(ctx: ToolContext, _args: GetDependencyStatusArgs) -> list[dict]:
    rows = (
        ctx.session.query(DependencyStatus)
        .filter(DependencyStatus.scenario_id == ctx.scenario.id)
        .order_by(DependencyStatus.dependency_name)
        .all()
    )
    return [
        {"dependency_name": r.dependency_name, "status": r.status, "checked_at": r.checked_at.isoformat()}
        for r in rows
    ]


def get_runbook(ctx: ToolContext, args: GetRunbookArgs) -> dict:
    keyword = args.symptom_keyword.lower()
    rows = ctx.session.query(Runbook).filter(Runbook.service_id == ctx.scenario.service_id).all()
    for r in rows:
        if keyword in r.symptom_keyword.lower() or r.symptom_keyword.lower() in keyword:
            return {"found": True, "symptom_keyword": r.symptom_keyword, "content": r.content}
    return {"found": False, "symptom_keyword": args.symptom_keyword, "content": None}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], list[dict] | dict]


TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "get_metrics",
            "Get time-series metric values (error_rate, latency_ms, cpu_pct, mem_pct) for "
            "this incident's service.",
            GetMetricsArgs,
            get_metrics,
        ),
        ToolSpec(
            "get_logs",
            "Get log entries for this incident's service, optionally filtered by level.",
            GetLogsArgs,
            get_logs,
        ),
        ToolSpec(
            "get_recent_deployments",
            "Get deployments to this incident's service in a recent time window.",
            GetRecentDeploymentsArgs,
            get_recent_deployments,
        ),
        ToolSpec(
            "get_dependency_status",
            "Get the health status of this incident's known dependencies.",
            GetDependencyStatusArgs,
            get_dependency_status,
        ),
        ToolSpec(
            "get_runbook",
            "Look up a runbook entry for this incident's service by symptom keyword.",
            GetRunbookArgs,
            get_runbook,
        ),
    )
}


def anthropic_tool_definitions() -> list[dict]:
    """JSON-schema tool definitions for the Anthropic Messages API, derived
    directly from the same Pydantic models that validate the arguments —
    one schema, not two definitions that can drift apart."""
    definitions = []
    for spec in TOOL_REGISTRY.values():
        schema = spec.args_model.model_json_schema()
        schema.pop("title", None)
        definitions.append(
            {"name": spec.name, "description": spec.description, "input_schema": schema}
        )
    return definitions
