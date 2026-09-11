import datetime as dt

from pydantic import BaseModel, ConfigDict


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
