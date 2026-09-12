"""End-to-end incident tests: create -> run -> assert the audit log is
complete and ordered -> assert the timeline the dashboard reads is coherent.
Also covers the Phase 3 security requirements: rate limiting, request-size
limits, and input validation on incident creation.
"""

import pytest

from opspilot.agent.loop import get_chat_fn
from opspilot.auth import reset_rate_limit_state
from opspilot.config import get_settings
from opspilot.main import app
from opspilot.seed.generator import seed_all
from tests.fakes import ScriptedChatFn, tool_use_response

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


@pytest.fixture(autouse=True)
def _ensure_seeded(db_session):
    # test_migrations.py wipes and rebuilds the schema; re-seed defensively
    # so this file's tests don't depend on running before/after that one.
    seed_all(db_session)


@pytest.fixture
def override_chat_fn():
    def _apply(scripted):
        app.dependency_overrides[get_chat_fn] = lambda: scripted

    yield _apply
    app.dependency_overrides.pop(get_chat_fn, None)


def test_create_incident_requires_existing_scenario(client, auth_headers):
    resp = client.post(
        "/api/v1/incidents", json={"scenario_key": "does-not-exist"}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_create_incident_rejects_malformed_scenario_key(client, auth_headers):
    resp = client.post(
        "/api/v1/incidents", json={"scenario_key": "Not Valid!"}, headers=auth_headers
    )
    assert resp.status_code == 422


def test_create_run_approve_end_to_end(client, auth_headers, override_chat_fn):
    """create -> run (pauses for approval) -> approve (executes) -> timeline
    shows the full, ordered, honest sequence — including the approval
    itself, not just the tool calls around it."""
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))

    create_resp = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    )
    assert create_resp.status_code == 201
    incident = create_resp.json()
    assert incident["status"] == "open"

    run_resp = client.post(f"/api/v1/incidents/{incident['id']}/run", headers=auth_headers)
    assert run_resp.status_code == 200
    paused = run_resp.json()
    assert paused["status"] == "awaiting_approval"
    assert paused["pending_approval"]["action_type"] == "rollback_deployment"
    assert paused["steps_used"] == 3

    approve_resp = client.post(
        f"/api/v1/incidents/{incident['id']}/approvals",
        json={"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    resolved = approve_resp.json()
    assert resolved["status"] == "diagnosed"
    assert resolved["diagnosis"]["recommended_action"] == "rollback_deployment"
    assert resolved["pending_approval"] is None

    timeline_resp = client.get(f"/api/v1/incidents/{incident['id']}/timeline", headers=auth_headers)
    assert timeline_resp.status_code == 200
    entries = timeline_resp.json()["entries"]

    kinds = [e["kind"] for e in entries]
    assert kinds[0] == "incident_started"
    assert kinds[-1] == "final_status"
    assert kinds.count("evidence_gathered") == 2
    assert kinds.count("diagnosis_formed") == 1
    assert kinds.count("approval_requested") == 1
    assert kinds.count("approval_decided") == 1

    decided = next(e for e in entries if e["kind"] == "approval_decided")
    assert "alice" in decided["label"]

    steps = [e["step"] for e in entries if e["step"] is not None]
    assert steps == sorted(steps)

    timestamps = [e["timestamp"] for e in entries]
    assert timestamps == sorted(timestamps)


def test_deny_pending_action_does_not_execute(client, auth_headers, override_chat_fn):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))
    incident_id = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    ).json()["id"]
    client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)

    deny_resp = client.post(
        f"/api/v1/incidents/{incident_id}/approvals",
        json={"approved": False, "actor": "bob"},
        headers=auth_headers,
    )
    assert deny_resp.status_code == 200
    resolved = deny_resp.json()
    assert resolved["status"] == "diagnosed"
    assert resolved["diagnosis"]["recommended_action"] == "rollback_deployment"

    timeline_resp = client.get(f"/api/v1/incidents/{incident_id}/timeline", headers=auth_headers)
    decided = next(e for e in timeline_resp.json()["entries"] if e["kind"] == "approval_decided")
    assert "Denied" in decided["label"]
    assert "bob" in decided["label"]


def test_approve_without_required_params_returns_422(client, auth_headers, override_chat_fn):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))
    incident_id = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    ).json()["id"]
    client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/approvals",
        json={"approved": True, "actor": "alice"},  # missing required target_version
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_approvals_endpoint_requires_awaiting_approval_status(client, auth_headers):
    incident_id = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    ).json()["id"]  # still "open" — never run

    resp = client.post(
        f"/api/v1/incidents/{incident_id}/approvals",
        json={"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}},
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_approve_nonexistent_incident_returns_404(client, auth_headers):
    resp = client.post(
        "/api/v1/incidents/999999/approvals",
        json={"approved": True, "actor": "alice"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_approval_sla_timeout_auto_escalates(client, auth_headers, override_chat_fn, monkeypatch):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))
    monkeypatch.setenv("APPROVAL_SLA_SECONDS", "0")
    get_settings.cache_clear()
    try:
        incident_id = client.post(
            "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
        ).json()["id"]
        client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)

        # Any subsequent read finds the pause already aged past a 0-second SLA.
        get_resp = client.get(f"/api/v1/incidents/{incident_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        resolved = get_resp.json()
        assert resolved["status"] == "diagnosed"
    finally:
        get_settings.cache_clear()

    timeline_resp = client.get(f"/api/v1/incidents/{incident_id}/timeline", headers=auth_headers)
    decided = next(e for e in timeline_resp.json()["entries"] if e["kind"] == "approval_decided")
    assert "timeout" in decided["label"].lower()


def test_run_incident_twice_is_rejected(client, auth_headers, override_chat_fn):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))

    create_resp = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    )
    incident_id = create_resp.json()["id"]

    first = client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)
    assert first.status_code == 200

    second = client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)
    assert second.status_code == 409


def test_get_and_list_incidents(client, auth_headers, override_chat_fn):
    override_chat_fn(ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT)))

    create_resp = client.post(
        "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
    )
    incident_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/v1/incidents/{incident_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == incident_id

    list_resp = client.get("/api/v1/incidents", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(i["id"] == incident_id for i in list_resp.json())


def test_run_nonexistent_incident_returns_404(client, auth_headers):
    resp = client.post("/api/v1/incidents/999999/run", headers=auth_headers)
    assert resp.status_code == 404


def test_request_body_too_large_is_rejected(client, auth_headers):
    settings = get_settings()
    oversized_key = "a" * (settings.max_request_body_bytes + 10)
    resp = client.post(
        "/api/v1/incidents", json={"scenario_key": oversized_key}, headers=auth_headers
    )
    assert resp.status_code == 413


def test_rate_limit_blocks_excessive_requests(client, auth_headers, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "3")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    reset_rate_limit_state()
    try:
        statuses = [
            client.get("/api/v1/services", headers=auth_headers).status_code for _ in range(5)
        ]
    finally:
        get_settings.cache_clear()
        reset_rate_limit_state()

    assert statuses == [200, 200, 200, 429, 429]
