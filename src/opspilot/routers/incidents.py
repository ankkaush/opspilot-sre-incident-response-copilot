"""Incident CRUD, the run/approval endpoints, and the timeline the
dashboard reads.

An Incident is a single, real attempt at investigating one seeded Scenario.
Creating one is cheap and reversible; running one is not (it calls the real
model and spends real money) — so `POST /incidents/{id}/run` refuses to run
an Incident twice. That's a small, deterministic safety rule of exactly the
kind this project keeps insisting on: code decides what's allowed, not the
caller's intent.

v0.2 Phase 3 adds the human-in-the-loop path: a run can come back
`awaiting_approval` instead of finished, and `POST /incidents/{id}/approvals`
is the only way to move it forward. A paused incident that nobody decides on
in time resolves itself — see `_maybe_apply_approval_timeout`.
"""

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.graph import resume_investigation, run_investigation
from opspilot.agent.loop import get_chat_fn
from opspilot.agent.remediation_tools import RollbackDeploymentArgs, ToggleFeatureFlagArgs
from opspilot.agent.schemas import InvestigationResult
from opspilot.agent.tracing import get_trace_url
from opspilot.auth import require_api_key
from opspilot.config import get_settings
from opspilot.db import get_db
from opspilot.memory import consolidate_service_memory, write_confirmed_memory
from opspilot.models import AuditLogEntry, Incident, Scenario
from opspilot.process_identity import PROCESS_INSTANCE_ID
from opspilot.schemas import (
    ApprovalDecision,
    IncidentCreate,
    IncidentDetail,
    TimelineEntry,
    TimelineResponse,
)

log = logging.getLogger("opspilot.incidents")

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

# Action types a human approval can carry concrete parameters for, and the
# schema those parameters are validated against before the graph is ever
# resumed — the API boundary is where this validation belongs, not the
# graph node (see agent/nodes.py's own defense-in-depth re-validation).
_APPROVAL_PARAM_MODELS = {
    "rollback_deployment": RollbackDeploymentArgs,
    "toggle_feature_flag": ToggleFeatureFlagArgs,
}


def _thread_id(incident_id: int) -> str:
    return f"incident-{incident_id}"


def _get_incident_or_404(db: Session, incident_id: int) -> Incident:
    incident = db.query(Incident).filter_by(id=incident_id).one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail=f"No incident with id {incident_id}.")
    return incident


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
        pending_approval=incident.pending_approval,
        awaiting_since=incident.awaiting_since,
        langfuse_trace_id=incident.langfuse_trace_id,
        langfuse_trace_url=get_trace_url(incident.langfuse_trace_id),
    )


def _persist_new_evidence(
    db: Session, incident: Incident, result: InvestigationResult, now: dt.datetime
) -> None:
    """Evidence_trail is cumulative across a pause/resume (the checkpointer
    carries it forward), so re-persisting it wholesale on resume would
    duplicate every row from before the pause. Only what's new since the
    last time this incident was persisted gets written."""
    already_persisted = db.query(AuditLogEntry).filter_by(incident_id=incident.id).count()
    for record in result.evidence_trail[already_persisted:]:
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


def _apply_result(db: Session, incident: Incident, result: InvestigationResult) -> None:
    now = dt.datetime.now(dt.UTC)
    _persist_new_evidence(db, incident, result, now)

    if result.status == "awaiting_approval":
        incident.status = "awaiting_approval"
        incident.pending_approval = result.pending_approval
        incident.awaiting_since = now
        incident.steps_used = result.steps_used
        incident.estimated_cost_usd = result.estimated_cost_usd
        incident.langfuse_trace_id = result.langfuse_trace_id
        db.add(
            AuditLogEntry(
                incident_id=incident.id,
                step=result.steps_used,
                tool_name="approval_requested",
                # v0.5 Phase 3 — stamped so a later resume from a *different*
                # process is a directly observable fact (see
                # opspilot.process_identity), not something only a demo
                # script's narration claims happened.
                arguments={**(result.pending_approval or {}), "process_instance_id": PROCESS_INSTANCE_ID},
                result=None,
                error=None,
                created_at=now,
            )
        )
        db.commit()
        return

    if incident.status == "awaiting_approval":
        # A pause is being resolved — record what was decided as its own
        # audit row, since evaluate_policy's interrupt/resume never touches
        # evidence_trail (there's nothing there to fall back on).
        db.add(
            AuditLogEntry(
                incident_id=incident.id,
                step=result.steps_used,
                tool_name="approval_decision",
                arguments={**(result.approval_decision or {}), "process_instance_id": PROCESS_INSTANCE_ID},
                result=result.remediation_result,
                error=None,
                created_at=now,
            )
        )

    incident.status = result.status
    incident.diagnosis = result.diagnosis.model_dump() if result.diagnosis else None
    incident.steps_used = result.steps_used
    incident.estimated_cost_usd = result.estimated_cost_usd
    incident.completed_at = now
    incident.pending_approval = None
    incident.awaiting_since = None
    incident.langfuse_trace_id = result.langfuse_trace_id

    # v0.4 Phase 1 — the incident is closing; this is the one moment memory
    # ever gets written (see opspilot.memory.write_confirmed_memory for the
    # write-policy gate that decides whether anything actually lands).
    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()
    written = write_confirmed_memory(
        db, service_id=scenario.service_id, incident_id=incident.id, result=result
    )
    if written is not None:
        consolidate_service_memory(db, scenario.service_id)

    db.commit()


def _maybe_apply_approval_timeout(db: Session, incident: Incident, chat_fn: ChatFn) -> None:
    """Lazy SLA-timeout check: no background scheduler (consistent with the
    project's "no queues/Redis" scope decision) — a paused incident that's
    aged past the SLA auto-escalates the moment anything next reads it."""
    if incident.status != "awaiting_approval" or incident.awaiting_since is None:
        return
    settings = get_settings()
    age_seconds = (dt.datetime.now(dt.UTC) - incident.awaiting_since).total_seconds()
    if age_seconds < settings.approval_sla_seconds:
        return

    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()
    decision = {"approved": False, "actor": "system:sla-timeout", "params": {}, "timed_out": True}
    try:
        result = resume_investigation(
            db, scenario, chat_fn=chat_fn, thread_id=_thread_id(incident.id), decision=decision
        )
    except Exception:
        # Never let a timeout-check side effect break a read. Worst case:
        # the incident stays awaiting_approval and the next read tries again.
        log.exception(
            "SLA-timeout auto-resolution failed", extra={"extra_fields": {"incident_id": incident.id}}
        )
        return

    _apply_result(db, incident, result)
    log.info(
        "incident auto-escalated after approval SLA timeout",
        extra={"extra_fields": {"incident_id": incident.id, "age_seconds": round(age_seconds)}},
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
def list_incidents(
    db: Session = Depends(get_db), chat_fn: ChatFn = Depends(get_chat_fn)
) -> list[IncidentDetail]:
    incidents = db.query(Incident).order_by(Incident.created_at.desc()).all()
    for incident in incidents:
        _maybe_apply_approval_timeout(db, incident, chat_fn)
    return [_to_incident_detail(db, i) for i in incidents]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(
    incident_id: int, db: Session = Depends(get_db), chat_fn: ChatFn = Depends(get_chat_fn)
) -> IncidentDetail:
    incident = _get_incident_or_404(db, incident_id)
    _maybe_apply_approval_timeout(db, incident, chat_fn)
    return _to_incident_detail(db, incident)


@router.post("/incidents/{incident_id}/run", response_model=IncidentDetail)
def run_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    chat_fn: ChatFn = Depends(get_chat_fn),
) -> IncidentDetail:
    incident = _get_incident_or_404(db, incident_id)
    if incident.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Incident {incident_id} has already been run (status: {incident.status}).",
        )

    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()

    incident.status = "running"
    incident.started_at = dt.datetime.now(dt.UTC)
    db.commit()

    result = run_investigation(db, scenario, chat_fn=chat_fn, thread_id=_thread_id(incident.id))
    _apply_result(db, incident, result)
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


@router.post("/incidents/{incident_id}/approvals", response_model=IncidentDetail)
def decide_approval(
    incident_id: int,
    body: ApprovalDecision,
    db: Session = Depends(get_db),
    chat_fn: ChatFn = Depends(get_chat_fn),
) -> IncidentDetail:
    incident = _get_incident_or_404(db, incident_id)
    _maybe_apply_approval_timeout(db, incident, chat_fn)
    if incident.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Incident {incident_id} is not awaiting approval (status: {incident.status}).",
        )

    action_type = (incident.pending_approval or {}).get("action_type")
    params = body.params or {}
    if body.approved and action_type in _APPROVAL_PARAM_MODELS:
        try:
            _APPROVAL_PARAM_MODELS[action_type].model_validate(params)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid params for approving '{action_type}': {exc}"
            ) from exc

    scenario = db.query(Scenario).filter_by(id=incident.scenario_id).one()
    decision = {"approved": body.approved, "actor": body.actor, "params": params}
    result = resume_investigation(
        db, scenario, chat_fn=chat_fn, thread_id=_thread_id(incident.id), decision=decision
    )
    _apply_result(db, incident, result)
    db.refresh(incident)

    log.info(
        "incident approval decided",
        extra={
            "extra_fields": {
                "incident_id": incident.id,
                "approved": body.approved,
                "actor": body.actor,
                "status": incident.status,
            }
        },
    )
    return _to_incident_detail(db, incident)


@router.get("/incidents/{incident_id}/timeline", response_model=TimelineResponse)
def get_timeline(
    incident_id: int, db: Session = Depends(get_db), chat_fn: ChatFn = Depends(get_chat_fn)
) -> TimelineResponse:
    incident = _get_incident_or_404(db, incident_id)
    _maybe_apply_approval_timeout(db, incident, chat_fn)
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
    # v0.5 Phase 3 — the process_instance_id stamped on "approval_requested"
    # (the pause) versus "approval_decision" (the resume): tracked across the
    # loop so the decision row can compare against the request row that
    # preceded it, since PROCESS_INSTANCE_ID isn't otherwise visible from
    # the decision row alone.
    pause_process_id: str | None = None
    for row in audit_rows:
        args = row.arguments or {}
        if row.tool_name == "submit_diagnosis":
            kind = "diagnosis_formed"
            label = "Diagnosis submitted" if row.error is None else "Diagnosis rejected — invalid"
        elif row.tool_name == "approval_requested":
            kind = "approval_requested"
            label = f"Approval requested for {args.get('action_type', 'an action')}"
            pause_process_id = args.get("process_instance_id")
        elif row.tool_name == "approval_decision":
            kind = "approval_decided"
            if args.get("timed_out"):
                label = "Approval auto-escalated after SLA timeout"
            else:
                verb = "Approved" if args.get("approved") else "Denied"
                label = f"{verb} by {args.get('actor', 'unknown')}"
            # v0.5 Phase 2 — a crash-and-resume (or a retried request) that
            # reached this same remediation a second time is made visible
            # here, not silently absorbed: opspilot.agent.idempotency
            # returns the first attempt's recorded result instead of
            # re-executing, and flags it in remediation_result itself.
            if isinstance(row.result, dict) and row.result.get("deduplicated"):
                label += " — remediation already executed once (crash-and-resume detected, not re-run)"
            # v0.5 Phase 3 — a different process_instance_id between the
            # pause and this decision is concrete evidence a process
            # restart happened while this incident sat awaiting approval,
            # made visible here rather than only inferable from logs.
            resume_process_id = args.get("process_instance_id")
            if pause_process_id is not None and resume_process_id != pause_process_id:
                label += " — resumed after a process restart"
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

    if incident.status not in ("open", "running", "awaiting_approval"):
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
