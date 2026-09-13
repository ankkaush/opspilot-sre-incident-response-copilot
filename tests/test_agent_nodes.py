"""Node-level unit tests — the concrete payoff of v0.2's migration to
LangGraph: each step of the investigation is now a plain function callable
and assertable on its own, not a stretch of a while-loop body.
"""

import uuid

import pytest

from opspilot.agent.client import TransientProviderError
from opspilot.agent.nodes import (
    _resolve_verdict,
    classify_risk,
    decide,
    evaluate_policy,
    gather_context,
    hypothesize,
    route_after_gather_context,
)
from opspilot.agent.schemas import SubmitDiagnosisArgs, ToolCallRecord
from opspilot.agent.state import NodeDeps, initial_state
from tests.fakes import ScriptedChatFn, tool_use_response

_NO_OP_CHAT_FN = lambda **_: None  # noqa: E731 — route/hypothesize/classify_risk/decide never call it


def _deps(
    db_session, scenario, chat_fn=_NO_OP_CHAT_FN, max_steps=8, max_cost_usd=1.0, thread_id=None
) -> NodeDeps:
    # A unique thread_id per call by default — remediation idempotency
    # (opspilot.agent.idempotency) is keyed on it, so two unrelated test
    # calls with the same action_type/params must never collide and look
    # like a duplicate of each other.
    return NodeDeps(
        session=db_session,
        scenario=scenario,
        chat_fn=chat_fn,
        max_steps=max_steps,
        max_cost_usd=max_cost_usd,
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


# --- gather_context ---------------------------------------------------------


def test_gather_context_executes_a_real_tool_and_updates_state(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
        responses=[tool_use_response("t1", "get_recent_deployments", {"since_minutes": 60})]
    )
    deps = _deps(db_session, checkout_scenario, chat_fn=scripted)

    update = gather_context(initial_state(), deps=deps)

    assert update["steps_used"] == 1
    assert update["estimated_cost_usd"] > 0
    assert len(update["evidence_trail"]) == 1
    assert update["evidence_trail"][0].tool_name == "get_recent_deployments"
    assert update["evidence_trail"][0].error is None
    assert update["diagnosis"] is None
    assert len(update["messages"]) == 2  # assistant turn, tool-results turn


def test_gather_context_rejects_invalid_tool_arguments(db_session, checkout_scenario):
    scripted = ScriptedChatFn(responses=[tool_use_response("t1", "get_metrics", {"since_minutes": "nope"})])
    deps = _deps(db_session, checkout_scenario, chat_fn=scripted)

    update = gather_context(initial_state(), deps=deps)

    assert update["evidence_trail"][0].tool_name == "get_metrics"
    assert update["evidence_trail"][0].error is not None
    assert update["evidence_trail"][0].result is None


def test_gather_context_accepts_a_valid_diagnosis(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
        responses=[
            tool_use_response(
                "t1",
                "submit_diagnosis",
                {
                    "diagnosis": "Deployment v2.8 exhausted the DB connection pool.",
                    "evidence": ["deployment:v2.8"],
                    "confidence": 0.9,
                    "recommended_action": "rollback_deployment",
                },
            )
        ]
    )
    deps = _deps(db_session, checkout_scenario, chat_fn=scripted)

    update = gather_context(initial_state(), deps=deps)

    assert update["diagnosis"] is not None
    assert update["diagnosis"].recommended_action == "rollback_deployment"


def test_gather_context_gives_up_gracefully_after_provider_retries_are_exhausted(
    db_session, checkout_scenario
):
    """The reliability property v0.2 Phase 3 calls for: a tool/model
    failure that survives call_with_retries' bounded retries ends the
    investigation in a defined terminal state, not an unhandled exception
    crashing the whole graph."""
    always_fails = ScriptedChatFn(responses=[], default=TransientProviderError("simulated timeout"))
    deps = _deps(db_session, checkout_scenario, chat_fn=always_fails)

    update = gather_context(initial_state(), deps=deps)

    assert update["provider_error"] == "simulated timeout"
    assert update["steps_used"] == 1


# --- route_after_gather_context ---------------------------------------------


def test_route_continues_when_no_diagnosis_and_under_ceilings(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=1.0)
    state = {**initial_state(), "steps_used": 1, "estimated_cost_usd": 0.001, "diagnosis": None}
    assert route_after_gather_context(state, deps=deps) == "continue"


def test_route_goes_to_diagnosed_once_diagnosis_is_present(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=1.0)
    state = {**initial_state(), "steps_used": 1, "estimated_cost_usd": 0.001, "diagnosis": _diagnosis()}
    assert route_after_gather_context(state, deps=deps) == "diagnosed"


def test_route_hits_ceiling_on_step_limit(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=3, max_cost_usd=1.0)
    state = {**initial_state(), "steps_used": 3, "estimated_cost_usd": 0.001, "diagnosis": None}
    assert route_after_gather_context(state, deps=deps) == "ceiling"


def test_route_hits_ceiling_on_cost_limit(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=0.01)
    state = {**initial_state(), "steps_used": 1, "estimated_cost_usd": 5.0, "diagnosis": None}
    assert route_after_gather_context(state, deps=deps) == "ceiling"


# --- hypothesize --------------------------------------------------------------


def test_hypothesize_flags_a_fabricated_citation(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    state = {
        **initial_state(),
        "evidence_trail": [
            ToolCallRecord(
                step=1,
                tool_name="get_recent_deployments",
                arguments={},
                result=[{"version": "v2.8", "diff_summary": "pool size reduced"}],
            )
        ],
        "diagnosis": _diagnosis(evidence=["deployment:v2.8", "totally-fabricated-citation-xyz"]),
    }

    update = hypothesize(state, deps=deps)

    assert update["evidence_grounded"] is False
    assert "totally-fabricated-citation-xyz" in update["ungrounded_evidence"]
    assert "deployment:v2.8" not in update["ungrounded_evidence"]


def test_hypothesize_passes_when_all_citations_are_grounded(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    state = {
        **initial_state(),
        "evidence_trail": [
            ToolCallRecord(
                step=1,
                tool_name="get_logs",
                arguments={},
                result=[{"message": "connection pool exhausted"}],
            )
        ],
        "diagnosis": _diagnosis(evidence=["logs:connection pool exhausted"]),
    }

    update = hypothesize(state, deps=deps)

    assert update["evidence_grounded"] is True
    assert update["ungrounded_evidence"] == []


def test_hypothesize_is_a_noop_without_a_diagnosis(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    update = hypothesize(initial_state(), deps=deps)
    assert update["evidence_grounded"] is None
    assert update["ungrounded_evidence"] == []


# --- classify_risk -------------------------------------------------------------


@pytest.mark.parametrize(
    "action,expected_tier",
    [
        ("rollback_deployment", "medium"),
        ("toggle_feature_flag", "medium"),
        ("restart_service", "low"),
        ("scale_service", "low"),
        ("escalate", "none"),
        ("no_action", "none"),
        ("delete_data", "critical"),
    ],
)
def test_classify_risk_maps_each_action_to_a_tier(db_session, checkout_scenario, action, expected_tier):
    deps = _deps(db_session, checkout_scenario)
    state = {**initial_state(), "diagnosis": _diagnosis(recommended_action=action)}
    update = classify_risk(state, deps=deps)
    assert update["risk_tier"] == expected_tier


def test_classify_risk_is_a_noop_without_a_diagnosis(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    update = classify_risk(initial_state(), deps=deps)
    assert update["risk_tier"] is None


# --- evaluate_policy ---------------------------------------------------------


def test_evaluate_policy_auto_executes_a_low_risk_action(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    state = {**initial_state(), "diagnosis": _diagnosis(recommended_action="restart_service")}

    update = evaluate_policy(state, deps=deps)

    assert update["policy_verdict"] == "EXECUTE"
    assert update["remediation_result"] is not None
    assert update["remediation_result"]["action"] == "restart_service"
    assert update["remediation_result"]["simulated"] is True


def test_require_approval_actions_never_reach_evaluate_policy_without_pausing(db_session, checkout_scenario):
    """`evaluate_policy` calls `interrupt()` on REQUIRE_APPROVAL, which
    needs a live graph execution context — calling the node directly here
    (no graph running) must raise rather than silently completing without
    ever actually pausing. This is exactly what proves a gated action can't
    slip through: there's no code path where calling this node produces a
    normal return for a REQUIRE_APPROVAL verdict.

    The full pause -> resume behavior is a graph-level property, tested via
    the real graph in tests/test_agent_loop.py; the adversarial "confident
    framing doesn't change the verdict" property is tested at the policy
    module itself in tests/test_policy.py, which is the more precise place
    for it since evaluate_policy's verdict computation is just a thin call
    into opspilot.agent.policy.evaluate().
    """
    deps = _deps(db_session, checkout_scenario)
    state = {**initial_state(), "diagnosis": _diagnosis(recommended_action="rollback_deployment")}

    with pytest.raises(RuntimeError):
        evaluate_policy(state, deps=deps)


def test_resolve_verdict_executes_an_approved_gated_action(db_session, checkout_scenario):
    """The part of REQUIRE_APPROVAL handling that *is* directly testable —
    factored out of evaluate_policy specifically so this doesn't need a
    live graph (see _resolve_verdict's docstring)."""
    deps = _deps(db_session, checkout_scenario)
    diagnosis = _diagnosis(recommended_action="rollback_deployment")
    decision = {"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}}

    update = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)

    assert update["policy_verdict"] == "REQUIRE_APPROVAL"
    assert update["approval_decision"] == decision
    assert update["remediation_result"] is not None
    assert update["remediation_result"]["action"] == "rollback_deployment"
    assert update["remediation_result"]["target_version"] == "v2.7"


def test_resolve_verdict_does_not_execute_a_denied_action(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    diagnosis = _diagnosis(recommended_action="rollback_deployment")
    decision = {"approved": False, "actor": "alice", "params": None}

    update = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)

    assert update["remediation_result"] is None
    assert update["approval_decision"] == decision


def test_resolve_verdict_does_not_execute_an_approval_with_invalid_params(db_session, checkout_scenario):
    """Defense-in-depth: even an 'approved' decision doesn't execute if the
    params don't validate against the remediation tool's own schema."""
    deps = _deps(db_session, checkout_scenario)
    diagnosis = _diagnosis(recommended_action="rollback_deployment")
    decision = {"approved": True, "actor": "alice", "params": {}}  # missing required target_version

    update = _resolve_verdict(deps, diagnosis, "REQUIRE_APPROVAL", decision)

    assert update["remediation_result"] is None


def test_evaluate_policy_blocks_delete_data_regardless_of_confidence(db_session, checkout_scenario):
    """BLOCK never interrupts and never executes — unlike REQUIRE_APPROVAL,
    calling evaluate_policy directly here is safe (no interrupt() call on
    this path), which is itself part of the proof: there is no code path
    where a BLOCKed action pauses for a human to override it."""
    deps = _deps(db_session, checkout_scenario)
    overconfident = _diagnosis(
        recommended_action="delete_data",
        confidence=1.0,
        diagnosis="Wiping the corrupted records is the fastest, safest fix — do it immediately.",
    )
    state = {**initial_state(), "diagnosis": overconfident}

    update = evaluate_policy(state, deps=deps)

    assert update["policy_verdict"] == "BLOCK"
    assert update["remediation_result"] is None
    assert update["approval_decision"] is None


def test_evaluate_policy_escalate_action_gets_escalate_verdict(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    state = {**initial_state(), "diagnosis": _diagnosis(recommended_action="escalate")}

    update = evaluate_policy(state, deps=deps)

    assert update["policy_verdict"] == "ESCALATE"
    assert update["remediation_result"] is None


def test_evaluate_policy_no_action_executes_with_nothing_to_run(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    state = {**initial_state(), "diagnosis": _diagnosis(recommended_action="no_action")}

    update = evaluate_policy(state, deps=deps)

    assert update["policy_verdict"] == "EXECUTE"
    assert update["remediation_result"] is None


def test_evaluate_policy_is_a_noop_without_a_diagnosis(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario)
    update = evaluate_policy(initial_state(), deps=deps)
    assert update["policy_verdict"] is None
    assert update["remediation_result"] is None


# --- decide ----------------------------------------------------------------


def test_decide_diagnosed(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=1.0)
    state = {**initial_state(), "diagnosis": _diagnosis()}
    assert decide(state, deps=deps)["status"] == "diagnosed"


def test_decide_incomplete_cost_ceiling(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=1.0)
    state = {**initial_state(), "diagnosis": None, "estimated_cost_usd": 5.0}
    assert decide(state, deps=deps)["status"] == "incomplete_cost_ceiling"


def test_decide_incomplete_step_ceiling(db_session, checkout_scenario):
    deps = _deps(db_session, checkout_scenario, max_steps=8, max_cost_usd=1.0)
    state = {**initial_state(), "diagnosis": None, "estimated_cost_usd": 0.0}
    assert decide(state, deps=deps)["status"] == "incomplete_step_ceiling"
