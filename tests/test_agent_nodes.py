"""Node-level unit tests — the concrete payoff of v0.2 Phase 1's migration
to LangGraph: each step of the investigation is now a plain function
callable and assertable on its own, not a stretch of a while-loop body.
"""

import pytest

from opspilot.agent.nodes import (
    classify_risk,
    decide,
    gather_context,
    hypothesize,
    route_after_gather_context,
)
from opspilot.agent.schemas import SubmitDiagnosisArgs, ToolCallRecord
from opspilot.agent.state import NodeDeps, initial_state
from tests.fakes import ScriptedChatFn, tool_use_response

_NO_OP_CHAT_FN = lambda **_: None  # noqa: E731 — route/hypothesize/classify_risk/decide never call it


def _deps(db_session, scenario, chat_fn=_NO_OP_CHAT_FN, max_steps=8, max_cost_usd=1.0) -> NodeDeps:
    return NodeDeps(
        session=db_session, scenario=scenario, chat_fn=chat_fn, max_steps=max_steps, max_cost_usd=max_cost_usd
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
