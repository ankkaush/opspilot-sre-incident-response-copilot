import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
