"""Read-only routes for inspecting the seeded synthetic environment.

This is not the incident-investigation API — that's v0.1 Phase 3. These
routes exist so Phase 1's "done when" (a seeded, inspectable synthetic
incident) is verifiable over HTTP, not just by querying Postgres directly,
and so there's more than one route to exercise the auth-required test on.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opspilot.auth import require_api_key
from opspilot.db import get_db
from opspilot.models import DependencyStatus, Deployment, LogEntry, MetricPoint, Scenario, Service
from opspilot.schemas import ScenarioDetail, ScenarioSummary, ServiceOut

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/services", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db)) -> list[Service]:
    return db.query(Service).order_by(Service.name).all()


@router.get("/scenarios", response_model=list[ScenarioSummary])
def list_scenarios(db: Session = Depends(get_db)) -> list[Scenario]:
    return db.query(Scenario).order_by(Scenario.key).all()


@router.get("/scenarios/{key}", response_model=ScenarioDetail)
def get_scenario(key: str, db: Session = Depends(get_db)) -> dict:
    scenario = db.query(Scenario).filter_by(key=key).one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"No scenario with key '{key}'.")

    return {
        "id": scenario.id,
        "key": scenario.key,
        "service_id": scenario.service_id,
        "title": scenario.title,
        "description": scenario.description,
        "injected_cause": scenario.injected_cause,
        "incident_started_at": scenario.incident_started_at,
        "ground_truth": scenario.ground_truth,
        "deployment_count": db.query(Deployment).filter_by(scenario_id=scenario.id).count(),
        "metric_point_count": db.query(MetricPoint).filter_by(scenario_id=scenario.id).count(),
        "log_entry_count": db.query(LogEntry).filter_by(scenario_id=scenario.id).count(),
        "dependency_status_count": db.query(DependencyStatus)
        .filter_by(scenario_id=scenario.id)
        .count(),
    }
