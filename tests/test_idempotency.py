"""v0.5 Phase 2: idempotent remediation execution.

Unit-level tests exercise `execute_idempotently` directly. The node-level
tests call `_resolve_verdict` twice with identical inputs — the precise,
correct simulation of "LangGraph re-entered evaluate_policy from the top
and reached the remediation call again," since that's the literal
mechanism (see evaluate_policy's own docstring in opspilot.agent.nodes)
rather than a hypothetical. `evaluate_policy` itself can't be called this
way outside a real graph (its `interrupt()` call requires one, exactly
like tests/test_agent_nodes.py's own verdict tests already established) —
`_resolve_verdict` is the extracted, interrupt-free half that's directly
testable, and it's also exactly where the remediation call actually lives.
"""

import datetime as dt
import uuid

import pytest

from opspilot.agent.idempotency import _idempotency_key, execute_idempotently
from opspilot.agent.loop import get_chat_fn
from opspilot.agent.nodes import _resolve_verdict
from opspilot.agent.remediation_tools import RollbackDeploymentArgs
from opspilot.agent.schemas import SubmitDiagnosisArgs
from opspilot.agent.state import NodeDeps
from opspilot.main import app
from opspilot.models import RemediationExecution
from tests.fakes import ScriptedChatFn, tool_use_response

_NO_OP_CHAT_FN = lambda **_: None  # noqa: E731


def _deps(db_session, scenario, thread_id=None) -> NodeDeps:
    return NodeDeps(
        session=db_session,
        scenario=scenario,
        chat_fn=_NO_OP_CHAT_FN,
        max_steps=8,
        max_cost_usd=1.0,
        thread_id=thread_id or f"test-{uuid.uuid4()}",
    )


def _diagnosis(**overrides) -> SubmitDiagnosisArgs:
    defaults = dict(
        diagnosis="A sufficiently long diagnosis string for validation.",
        evidence=["deployment:v2.8"],
        confidence=0.8,
        recommended_action="rollback_deployment",
    )
    defaults.update(overrides)
    return SubmitDiagnosisArgs(**defaults)


# --- execute_idempotently, in isolation -------------------------------------


def test_first_call_executes_and_is_not_flagged_deduplicated():
    calls = []
    result, deduplicated = execute_idempotently(
        thread_id="idem-thread-1",
        action_type="restart_service",
        target="checkout-api",
        params={},
        executor=lambda: (calls.append(1), {"outcome": "restarted"})[1],
    )
    assert result == {"outcome": "restarted"}
    assert deduplicated is False
    assert len(calls) == 1


def test_second_call_with_identical_key_returns_cached_result_without_reexecuting():
    calls = []

    def executor():
        calls.append(1)
        return {"outcome": "restarted", "call_number": len(calls)}

    kwargs = dict(thread_id="idem-thread-2", action_type="restart_service", target="checkout-api", params={})
    first_result, first_dedup = execute_idempotently(**kwargs, executor=executor)
    second_result, second_dedup = execute_idempotently(**kwargs, executor=executor)

    assert first_dedup is False
    assert second_dedup is True
    # The executor only ever actually ran once — the second call's result is
    # the *first* call's recorded output, not a fresh invocation.
    assert len(calls) == 1
    assert second_result == first_result == {"outcome": "restarted", "call_number": 1}


def test_different_thread_ids_never_collide():
    calls = []
    executor = lambda: (calls.append(1), {"outcome": "restarted"})[1]  # noqa: E731

    execute_idempotently(
        thread_id="idem-thread-a", action_type="restart_service", target="checkout-api", params={},
        executor=executor,
    )
    execute_idempotently(
        thread_id="idem-thread-b", action_type="restart_service", target="checkout-api", params={},
        executor=executor,
    )
    assert len(calls) == 2


def test_different_params_never_collide():
    calls = []
    executor = lambda: (calls.append(1), {"outcome": "rolled back"})[1]  # noqa: E731

    execute_idempotently(
        thread_id="idem-thread-c", action_type="rollback_deployment", target="checkout-api",
        params={"target_version": "v2.6"}, executor=executor,
    )
    execute_idempotently(
        thread_id="idem-thread-c", action_type="rollback_deployment", target="checkout-api",
        params={"target_version": "v2.7"}, executor=executor,
    )
    assert len(calls) == 2


# --- Node-level re-entry: the actual mechanism at risk ----------------------


def test_require_approval_remediation_runs_exactly_once_across_reentry(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, thread_id="reentry-require-approval")
    diagnosis = _diagnosis(recommended_action="rollback_deployment")
    decision = {"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}}

    first = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)
    second = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)

    assert first["remediation_result"]["deduplicated"] is False
    assert second["remediation_result"]["deduplicated"] is True
    assert second["remediation_result"]["target_version"] == "v2.7"

    rows = (
        db_session.query(RemediationExecution)
        .filter_by(thread_id=deps.thread_id, action_type="rollback_deployment")
        .all()
    )
    assert len(rows) == 1


def test_execute_verdict_remediation_runs_exactly_once_across_reentry(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, thread_id="reentry-execute")
    diagnosis = _diagnosis(recommended_action="restart_service", confidence=0.6)

    first = _resolve_verdict(deps, diagnosis, "EXECUTE", None)
    second = _resolve_verdict(deps, diagnosis, "EXECUTE", None)

    assert first["remediation_result"]["deduplicated"] is False
    assert second["remediation_result"]["deduplicated"] is True

    rows = (
        db_session.query(RemediationExecution)
        .filter_by(thread_id=deps.thread_id, action_type="restart_service")
        .all()
    )
    assert len(rows) == 1


def test_denied_approval_never_claims_an_idempotency_key(db_session, checkout_scenario):
    """A denied action never executes at all — nothing to guard, nothing
    written. Re-asserted here since the guard sits inside
    _execute_approved_action, which _resolve_verdict only calls when
    decision['approved'] is true."""
    deps = _deps(db_session, checkout_scenario, thread_id="reentry-denied")
    diagnosis = _diagnosis(recommended_action="rollback_deployment")
    decision = {"approved": False, "actor": "bob", "params": {}}

    result = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)

    assert result["remediation_result"] is None
    assert (
        db_session.query(RemediationExecution).filter_by(thread_id=deps.thread_id).count() == 0
    )


_ROLLBACK_DECISION = {"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}}


@pytest.mark.parametrize(
    ("action", "verdict", "decision", "confidence"),
    [
        ("rollback_deployment", "REQUIRE_APPROVAL", _ROLLBACK_DECISION, 0.9),
        ("restart_service", "EXECUTE", None, 0.6),
    ],
)
def test_two_different_incidents_never_deduplicate_against_each_other(
    db_session, checkout_scenario, action, verdict, decision, confidence
):
    """Isolation in the other direction from the reentry tests above: two
    genuinely different investigations recommending the identical action
    must each execute — the idempotency key includes thread_id precisely
    so this never looks like a duplicate."""
    deps_a = _deps(db_session, checkout_scenario, thread_id="incident-101")
    deps_b = _deps(db_session, checkout_scenario, thread_id="incident-102")
    diagnosis = _diagnosis(recommended_action=action, confidence=confidence)

    result_a = _resolve_verdict(deps_a, diagnosis, verdict, decision)
    result_b = _resolve_verdict(deps_b, diagnosis, verdict, decision)

    assert result_a["remediation_result"]["deduplicated"] is False
    assert result_b["remediation_result"]["deduplicated"] is False


# --- Audit-trail visibility: "not silently" ---------------------------------

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


def test_deduplicated_remediation_is_visible_in_the_audit_trail(client, auth_headers, db_session):
    """A prior (e.g. crashed) attempt already having claimed this exact
    idempotency key — pre-seeded here to stand in for that crashed attempt
    — must be visible in the timeline the very first time *this* approve
    call goes through, not just logged somewhere internal."""
    app.dependency_overrides[get_chat_fn] = lambda: ScriptedChatFn(responses=list(HAPPY_PATH_SCRIPT))
    try:
        incident_id = client.post(
            "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
        ).json()["id"]
        client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)

        params = {"target_version": "v2.7"}
        validated = RollbackDeploymentArgs.model_validate(params)
        key = _idempotency_key(
            thread_id=f"incident-{incident_id}",
            action_type="rollback_deployment",
            target="checkout-api",
            params=validated.model_dump(),
        )
        db_session.add(
            RemediationExecution(
                idempotency_key=key,
                thread_id=f"incident-{incident_id}",
                action_type="rollback_deployment",
                target="checkout-api",
                params=validated.model_dump(),
                result={
                    "simulated": True,
                    "action": "rollback_deployment",
                    "service": "checkout-api",
                    "target_version": "v2.7",
                    "outcome": "rollback executed against the synthetic environment",
                },
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        db_session.commit()

        approve_resp = client.post(
            f"/api/v1/incidents/{incident_id}/approvals",
            json={"approved": True, "actor": "alice", "params": params},
            headers=auth_headers,
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "diagnosed"

        timeline = client.get(
            f"/api/v1/incidents/{incident_id}/timeline", headers=auth_headers
        ).json()["entries"]
        decided = next(e for e in timeline if e["kind"] == "approval_decided")

        assert "crash-and-resume detected" in decided["label"]
        assert decided["detail"]["result"]["deduplicated"] is True
    finally:
        app.dependency_overrides.pop(get_chat_fn, None)
