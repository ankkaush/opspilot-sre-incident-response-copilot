"""Service-scoped memory: the write-policy gate, outcome derivation,
consolidation, cross-service isolation, and the admin correction endpoint.

Unit-level tests build an InvestigationResult directly rather than running a
full investigation — write_confirmed_memory only ever reads that structured
object, never a live model response, so that's the right level to test it at.
"""

import datetime as dt
import uuid

import pytest

from opspilot.agent.loop import get_chat_fn
from opspilot.agent.schemas import InvestigationResult, SubmitDiagnosisArgs
from opspilot.main import app
from opspilot.memory import consolidate_service_memory, list_service_memory, write_confirmed_memory
from opspilot.models import Incident, Service, ServiceMemory
from opspilot.seed.generator import seed_all
from tests.fakes import ScriptedChatFn, text_response, tool_use_response


@pytest.fixture(autouse=True)
def _ensure_seeded(db_session):
    seed_all(db_session)


@pytest.fixture
def make_service(db_session):
    """A fresh, uniquely-named Service per call — so isolation/consolidation
    assertions (which count *all* rows for a service) never collide with
    memory some other test in this shared-database test session already
    wrote for the seeded checkout-api/payments-api services."""

    def _make() -> int:
        service = Service(name=f"test-svc-{uuid.uuid4().hex[:8]}", description="")
        db_session.add(service)
        db_session.flush()
        return service.id

    return _make


@pytest.fixture
def make_incident(db_session, checkout_scenario):
    """A real Incident row (service_memory.source_incident_id is a genuine
    foreign key) so these tests don't rely on some other test file having
    coincidentally already created an incident with a given id."""

    def _make() -> int:
        incident = Incident(
            scenario_id=checkout_scenario.id, status="open", created_at=dt.datetime.now(dt.UTC)
        )
        db_session.add(incident)
        db_session.flush()
        return incident.id

    return _make


def _diagnosed_result(
    *,
    confidence: float = 0.9,
    recommended_action: str = "rollback_deployment",
    policy_verdict: str = "REQUIRE_APPROVAL",
    approval_decision: dict | None = None,
    evidence: list[str] | None = None,
) -> InvestigationResult:
    return InvestigationResult(
        scenario_key="checkout-deploy-outage",
        status="diagnosed",
        diagnosis=SubmitDiagnosisArgs(
            diagnosis="Deployment v2.8 exhausted the DB connection pool.",
            evidence=evidence or ["deployment:v2.8", "logs:connection pool exhausted"],
            confidence=confidence,
            recommended_action=recommended_action,
        ),
        steps_used=3,
        estimated_cost_usd=0.01,
        policy_verdict=policy_verdict,
        approval_decision=approval_decision,
    )


def test_write_confirmed_memory_writes_one_row_for_confirmed_diagnosis(
    db_session, make_service, make_incident
):
    service_id = make_service()
    result = _diagnosed_result(approval_decision={"approved": True, "actor": "alice"})
    written = write_confirmed_memory(
        db_session, service_id=service_id, incident_id=make_incident(), result=result
    )
    db_session.commit()

    assert written is not None
    assert written.service_id == service_id
    assert written.fix_applied == "rollback_deployment"
    assert written.root_cause == result.diagnosis.diagnosis
    assert written.confidence == 0.9
    assert written.occurrence_count == 1

    rows = list_service_memory(db_session, service_id)
    assert len(rows) == 1
    assert rows[0].id == written.id


def test_write_confirmed_memory_skips_escalate_recommendation(db_session, make_service):
    service_id = make_service()
    result = _diagnosed_result(recommended_action="escalate", policy_verdict="ESCALATE", confidence=0.95)
    written = write_confirmed_memory(db_session, service_id=service_id, incident_id=1, result=result)
    assert written is None
    assert list_service_memory(db_session, service_id) == []


def test_write_confirmed_memory_skips_low_confidence(db_session, make_service):
    service_id = make_service()
    result = _diagnosed_result(confidence=0.4)
    written = write_confirmed_memory(db_session, service_id=service_id, incident_id=1, result=result)
    assert written is None
    assert list_service_memory(db_session, service_id) == []


def test_write_confirmed_memory_skips_missing_diagnosis(db_session, make_service):
    result = InvestigationResult(
        scenario_key="checkout-deploy-outage",
        status="incomplete_step_ceiling",
        diagnosis=None,
        steps_used=8,
        estimated_cost_usd=0.05,
    )
    written = write_confirmed_memory(
        db_session, service_id=make_service(), incident_id=1, result=result
    )
    assert written is None


@pytest.mark.parametrize(
    ("policy_verdict", "approval_decision", "expected_outcome"),
    [
        ("EXECUTE", None, "executed"),
        ("BLOCK", None, "blocked"),
        ("REQUIRE_APPROVAL", {"approved": True, "actor": "alice"}, "approved_and_executed"),
        ("REQUIRE_APPROVAL", {"approved": False, "actor": "bob"}, "denied"),
    ],
)
def test_outcome_is_derived_from_policy_verdict_and_approval(
    db_session, make_service, make_incident, policy_verdict, approval_decision, expected_outcome
):
    result = _diagnosed_result(policy_verdict=policy_verdict, approval_decision=approval_decision)
    written = write_confirmed_memory(
        db_session, service_id=make_service(), incident_id=make_incident(), result=result
    )
    assert written.outcome == expected_outcome


def test_cross_service_isolation_is_structural(db_session, make_service, make_incident):
    """Adversarial: even with the exact same symptom_pattern/fix_applied on
    both services, a lookup scoped to one service must never return the
    other's rows."""
    service_a, service_b = make_service(), make_service()
    for service_id in (service_a, service_b):
        write_confirmed_memory(
            db_session,
            service_id=service_id,
            incident_id=make_incident(),
            result=_diagnosed_result(approval_decision={"approved": True, "actor": "alice"}),
        )
    db_session.commit()

    rows_a = list_service_memory(db_session, service_a)
    rows_b = list_service_memory(db_session, service_b)

    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0].service_id == service_a
    assert rows_b[0].service_id == service_b
    assert rows_a[0].id != rows_b[0].id


def test_consolidate_merges_duplicates_and_keeps_max_confidence(db_session, make_service, make_incident):
    service_id = make_service()
    for confidence in (0.7, 0.95, 0.8):
        write_confirmed_memory(
            db_session,
            service_id=service_id,
            incident_id=make_incident(),
            result=_diagnosed_result(
                confidence=confidence, approval_decision={"approved": True, "actor": "alice"}
            ),
        )
    db_session.commit()
    assert len(list_service_memory(db_session, service_id)) == 3

    removed = consolidate_service_memory(db_session, service_id)
    db_session.commit()

    assert removed == 2
    rows = list_service_memory(db_session, service_id)
    assert len(rows) == 1
    assert rows[0].confidence == 0.95
    assert rows[0].occurrence_count == 3


def test_consolidate_leaves_distinct_patterns_untouched(db_session, make_service, make_incident):
    service_id = make_service()
    write_confirmed_memory(
        db_session,
        service_id=service_id,
        incident_id=make_incident(),
        result=_diagnosed_result(approval_decision={"approved": True, "actor": "alice"}),
    )
    write_confirmed_memory(
        db_session,
        service_id=service_id,
        incident_id=make_incident(),
        result=_diagnosed_result(
            recommended_action="restart_service",
            policy_verdict="EXECUTE",
            evidence=["metrics:cpu_pct"],
        ),
    )
    db_session.commit()

    removed = consolidate_service_memory(db_session, service_id)
    db_session.commit()

    assert removed == 0
    assert len(list_service_memory(db_session, service_id)) == 2


# --- Integration: incident close is the one write trigger -----------------

HAPPY_PATH_SCRIPT = [
    tool_use_response("t1", "get_recent_deployments", {"since_minutes": 60}),
    tool_use_response("t2", "get_logs", {"level": "error", "since_minutes": 60}),
    tool_use_response(
        "t3",
        "submit_diagnosis",
        {
            "diagnosis": "Deployment v2.8 exhausted the DB connection pool.",
            "evidence": ["deployment:v2.8", "logs:connection pool exhausted"],
            "confidence": 0.9,
            "recommended_action": "rollback_deployment",
        },
    ),
]

@pytest.fixture
def override_chat_fn():
    def _apply(scripted):
        app.dependency_overrides[get_chat_fn] = lambda: scripted

    yield _apply
    app.dependency_overrides.pop(get_chat_fn, None)


def test_incident_close_writes_memory_via_api(
    client, auth_headers, override_chat_fn, db_session, checkout_scenario
):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))

    incident_id = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    ).json()["id"]
    client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)
    approve_resp = client.post(
        f"/api/v1/incidents/{incident_id}/approvals",
        json={"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200

    rows = (
        db_session.query(ServiceMemory)
        .filter_by(service_id=checkout_scenario.service_id, source_incident_id=incident_id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].fix_applied == "rollback_deployment"
    assert rows[0].outcome == "approved_and_executed"


def test_incomplete_incident_writes_no_memory(client, auth_headers, override_chat_fn, db_session):
    override_chat_fn(ScriptedChatFn(responses=[], default=text_response("still investigating...")))

    before = db_session.query(ServiceMemory).count()
    incident_id = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    ).json()["id"]
    run_resp = client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)
    assert run_resp.json()["status"] == "incomplete_step_ceiling"

    after = db_session.query(ServiceMemory).count()
    assert after == before


# --- Admin correction endpoint ---------------------------------------------


def test_delete_memory_entry_requires_auth(client):
    resp = client.delete("/api/v1/memory/1")
    assert resp.status_code == 401


def test_delete_nonexistent_memory_entry_returns_404(client, auth_headers):
    resp = client.delete("/api/v1/memory/999999", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_memory_entry_removes_it(client, auth_headers, db_session, make_service, make_incident):
    written = write_confirmed_memory(
        db_session,
        service_id=make_service(),
        incident_id=make_incident(),
        result=_diagnosed_result(approval_decision={"approved": True, "actor": "alice"}),
    )
    db_session.commit()
    memory_id = written.id

    resp = client.delete(f"/api/v1/memory/{memory_id}", headers=auth_headers)
    assert resp.status_code == 204

    assert db_session.query(ServiceMemory).filter_by(id=memory_id).one_or_none() is None
