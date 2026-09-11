"""Incident CRUD, the run endpoint, and the timeline the dashboard reads.

An Incident is a single, real attempt at investigating one seeded Scenario.
Creating one is cheap and reversible; running one is not (it calls the real
model and spends real money) — so `POST /incidents/{id}/run` refuses to run
an Incident twice. That's a small, deterministic safety rule of exactly the
kind this project keeps insisting on: code decides what's allowed, not the
caller's intent.
"""

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.loop import get_chat_fn, investigate
from opspilot.auth import require_api_key
from opspilot.db import get_db
from opspilot.models import AuditLogEntry, Incident, Scenario
from opspilot.schemas import IncidentCreate, IncidentDetail, TimelineEntry, TimelineResponse

log = logging.getLogger("opspilot.incidents")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


def _to_incident_detail(db: Session, incident: Incident) -> IncidentDetail:
    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()
    return IncidentDetail(
        id=incident.id,
        scenario_key=scenario.key,
        service_name=scenario.service.name,
        status=incident.status,
        created_at=incident.created_at,
        steps_used=incident.steps_used,
        estimated_cost_usd=incident.estimated_cost_usd,
        diagnosis=incident.diagnosis,
        started_at=incident.started_at,
        completed_at=incident.completed_at,
    )


@router.post("/incidents", response_model=IncidentDetail, status_code=201)
def create_incident(body: IncidentCreate, db: Session = Depends(get_db)) -> IncidentDetail:
    scenario = db.query(Scenario).filter_by(key=body.scenario_key).one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"No scenario with key '{body.scenario_key}'.")

    incident = Incident(
        scenario_id=scenario.id,
        status="open",
        created_at=dt.datetime.now(dt.UTC),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return _to_incident_detail(db, incident)


@router.get("/incidents", response_model=list[IncidentDetail])
def list_incidents(db: Session = Depends(get_db)) -> list[IncidentDetail]:
    incidents = db.query(Incident).order_by(Incident.created_at.desc()).all()
    return [_to_incident_detail(db, i) for i in incidents]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, db: Session = Depends(get_db)) -> IncidentDetail:
    incident = db.query(Incident).filter_by(id=incident_id).one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail=f"No incident with id {incident_id}.")
    return _to_incident_detail(db, incident)


@router.post("/incidents/{incident_id}/run", response_model=IncidentDetail)
def run_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    chat_fn: ChatFn = Depends(get_chat_fn),
) -> IncidentDetail:
    incident = db.query(Incident).filter_by(id=incident_id).one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail=f"No incident with id {incident_id}.")
    if incident.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Incident {incident_id} has already been run (status: {incident.status}).",
        )

    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()

    incident.status = "running"
    incident.started_at = dt.datetime.now(dt.UTC)
    db.commit()

    result = investigate(db, scenario, chat_fn=chat_fn)

    now = dt.datetime.now(dt.UTC)
    for record in result.evidence_trail:
        db.add(
            AuditLogEntry(
                incident_id=incident.id,
                step=record.step,
                tool_name=record.tool_name,
                arguments=record.arguments,
                result=record.result,
                error=record.error,
                created_at=now,
            )
        )

    incident.status = result.status
    incident.diagnosis = result.diagnosis.model_dump() if result.diagnosis else None
    incident.steps_used = result.steps_used
    incident.estimated_cost_usd = result.estimated_cost_usd
    incident.completed_at = now
    db.commit()
    db.refresh(incident)

    log.info(
        "incident run complete",
        extra={
            "extra_fields": {
                "incident_id": incident.id,
                "scenario_key": scenario.key,
                "status": incident.status,
                "steps_used": incident.steps_used,
            }
        },
    )
    return _to_incident_detail(db, incident)


@router.get("/incidents/{incident_id}/timeline", response_model=TimelineResponse)
def get_timeline(incident_id: int, db: Session = Depends(get_db)) -> TimelineResponse:
    incident = db.query(Incident).filter_by(id=incident_id).one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail=f"No incident with id {incident_id}.")
    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()

    entries: list[TimelineEntry] = [
        TimelineEntry(
            kind="incident_started",
            step=None,
            label=f"Incident opened for {scenario.service.name}: {scenario.title}",
            detail=None,
            timestamp=incident.created_at,
        )
    ]

    audit_rows = (
        db.query(AuditLogEntry)
        .filter_by(incident_id=incident.id)
        .order_by(AuditLogEntry.step, AuditLogEntry.id)
        .all()
    )
    for row in audit_rows:
        if row.tool_name == "submit_diagnosis":
            kind = "diagnosis_formed"
            label = "Diagnosis submitted" if row.error is None else "Diagnosis rejected — invalid"
        else:
            kind = "evidence_gathered"
            label = f"Called {row.tool_name}" if row.error is None else f"Called {row.tool_name} — error"
        entries.append(
            TimelineEntry(
                kind=kind,
                step=row.step,
                label=label,
                detail={"arguments": row.arguments, "result": row.result, "error": row.error},
                timestamp=row.created_at,
            )
        )

    if incident.status not in ("open", "running"):
        entries.append(
            TimelineEntry(
                kind="final_status",
                step=None,
                label=f"Incident closed: {incident.status}",
                detail=incident.diagnosis,
                timestamp=incident.completed_at,
            )
        )

    return TimelineResponse(incident_id=incident.id, entries=entries)
