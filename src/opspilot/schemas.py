import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opspilot.eval.deterministic import DeterministicScores
from opspilot.eval.judge import JudgeScores
from opspilot.eval.runner import AggregateScores


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str


class GroundTruth(BaseModel):
    expected_evidence: list[str]
    expected_diagnosis: str
    expected_action: str
    expected_policy_verdict: str
    notes: str = ""


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    service_id: int
    title: str
    incident_started_at: dt.datetime


class ScenarioDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    service_id: int
    title: str
    description: str
    injected_cause: str
    incident_started_at: dt.datetime
    ground_truth: GroundTruth
    deployment_count: int
    metric_point_count: int
    log_entry_count: int
    dependency_status_count: int


class IncidentCreate(BaseModel):
    scenario_key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9-]+$",
        description="Key of a seeded scenario to investigate, e.g. 'checkout-deploy-outage'.",
    )


class IncidentSummary(BaseModel):
    id: int
    scenario_key: str
    service_name: str
    status: str
    created_at: dt.datetime
    steps_used: int | None
    estimated_cost_usd: float | None


class IncidentDetail(IncidentSummary):
    diagnosis: dict | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    # v0.2 Phase 3 — set while status == "awaiting_approval": what's being
    # asked, of whom, and why (the interrupt payload). awaiting_since
    # anchors the SLA-timeout check.
    pending_approval: dict | None = None
    awaiting_since: dt.datetime | None = None
    # v0.3 Phase 3 — the trace id from this incident's most recent run/resume
    # call, and the ready-to-click Langfuse URL for it (None whenever
    # tracing isn't configured; never required for the incident itself to
    # have run correctly).
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    actor: str = Field(
        min_length=1,
        max_length=128,
        description="Who made this decision — recorded in the audit trail.",
    )
    # Required for rollback_deployment (target_version) and
    # toggle_feature_flag (flag_name) — validated against the matching
    # remediation tool's own arg schema before the graph is ever resumed.
    # Ignored (and safe to omit) for every other action type.
    params: dict | None = None


class TimelineEntry(BaseModel):
    kind: Literal[
        "incident_started",
        "evidence_gathered",
        "diagnosis_formed",
        "approval_requested",
        "approval_decided",
        "final_status",
    ]
    step: int | None
    label: str
    detail: dict | None
    timestamp: dt.datetime | None


class TimelineResponse(BaseModel):
    incident_id: int
    entries: list[TimelineEntry]


# v0.3 Phase 3 — the agent dashboard's data: eval runs, read from
# opspilot.eval.storage's on-disk JSON files (not a DB table), with a
# ready-to-click Langfuse trace URL attached per scenario.


class EvalRunSummary(BaseModel):
    run_label: str
    started_at: dt.datetime
    scenario_count: int
    completion_rate: float
    policy_verdict_accuracy: float
    mean_diagnosis_accuracy: float | None
    total_cost_usd: float
    mean_latency_seconds: float


class EvalScenarioResultOut(BaseModel):
    scenario_key: str
    status: str
    deterministic: DeterministicScores
    judge: JudgeScores | None = None
    judge_error: str | None = None
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None


class EvalRunOut(BaseModel):
    run_label: str
    started_at: dt.datetime
    scenario_keys: list[str]
    scenarios: list[EvalScenarioResultOut]
    aggregate: AggregateScores


# v0.4 Phase 2 — the per-service "what does the agent know" dashboard view.
class ServiceMemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    source_incident_id: int
    symptom_pattern: str
    root_cause: str
    fix_applied: str
    outcome: str
    confidence: float
    occurrence_count: int
    created_at: dt.datetime
