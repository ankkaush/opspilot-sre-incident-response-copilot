"""Unit tests for the deterministic eval checks — pure functions of a
ScenarioSpec and an InvestigationResult, no DB, no model call, no cost."""

from opspilot.agent.schemas import InvestigationResult, SubmitDiagnosisArgs, ToolCallRecord
from opspilot.eval.deterministic import score_deterministic
from opspilot.seed.scenarios import CHECKOUT_DEPLOY_OUTAGE, PAYMENTS_DB_LATENCY


def _diagnosis(**overrides) -> SubmitDiagnosisArgs:
    defaults = dict(
        diagnosis="A sufficiently long diagnosis string for validation.",
        evidence=["deployment:checkout-api:v2.8"],
        confidence=0.8,
        recommended_action="rollback_deployment",
    )
    defaults.update(overrides)
    return SubmitDiagnosisArgs(**defaults)


def _result(**overrides) -> InvestigationResult:
    defaults = dict(
        scenario_key="checkout-deploy-outage",
        status="diagnosed",
        diagnosis=_diagnosis(),
        evidence_trail=[],
        steps_used=1,
        estimated_cost_usd=0.001,
        policy_verdict="REQUIRE_APPROVAL",
        evidence_grounded=True,
    )
    defaults.update(overrides)
    return InvestigationResult(**defaults)


def test_perfect_run_scores_fully_correct():
    trail = [
        ToolCallRecord(step=1, tool_name="get_recent_deployments", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_metrics", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_logs", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_dependency_status", arguments={}, result=[]),
        ToolCallRecord(
            step=2, tool_name="submit_diagnosis", arguments={}, result=_diagnosis().model_dump()
        ),
    ]
    result = _result(evidence_trail=trail, steps_used=2)

    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.5)

    assert scores.reached_diagnosis is True
    assert scores.policy_verdict_correct is True
    assert scores.recommended_action_exact_match is True
    assert scores.tool_selection_score == 1.0
    assert scores.unnecessary_tool_call_count == 0
    assert scores.argument_error_count == 0
    assert scores.evidence_grounded_heuristic is True
    assert scores.latency_seconds == 1.5


def test_wrong_policy_verdict_is_flagged():
    result = _result(policy_verdict="EXECUTE")  # ground truth expects REQUIRE_APPROVAL
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.policy_verdict_correct is False


def test_wrong_recommended_action_is_flagged():
    result = _result(diagnosis=_diagnosis(recommended_action="escalate"))
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.recommended_action_exact_match is False


def test_awaiting_approval_counts_as_reached_with_require_approval_verdict():
    result = _result(status="awaiting_approval", policy_verdict=None)
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.reached_diagnosis is True
    assert scores.policy_verdict_correct is True  # ground truth is REQUIRE_APPROVAL too


def test_incomplete_run_has_no_comparison_fields():
    result = _result(status="incomplete_step_ceiling", diagnosis=None, policy_verdict=None)
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.reached_diagnosis is False
    assert scores.policy_verdict_correct is None
    assert scores.recommended_action_exact_match is None


def test_missing_expected_tool_call_lowers_tool_selection_score():
    # checkout-deploy-outage expects deployment + metrics + logs + dependency_status
    trail = [ToolCallRecord(step=1, tool_name="get_recent_deployments", arguments={}, result=[])]
    result = _result(evidence_trail=trail)
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert 0.0 < scores.tool_selection_score < 1.0


def test_unnecessary_tool_calls_are_counted():
    trail = [
        ToolCallRecord(step=1, tool_name="get_recent_deployments", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_metrics", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_logs", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_dependency_status", arguments={}, result=[]),
        ToolCallRecord(step=1, tool_name="get_runbook", arguments={}, result={}),  # not in expected_evidence
    ]
    result = _result(evidence_trail=trail)
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.unnecessary_tool_call_count == 1


def test_argument_errors_are_counted():
    trail = [
        ToolCallRecord(step=1, tool_name="get_metrics", arguments={}, error="Invalid arguments"),
        ToolCallRecord(step=1, tool_name="get_logs", arguments={}, result=[]),
    ]
    result = _result(evidence_trail=trail)
    scores = score_deterministic(CHECKOUT_DEPLOY_OUTAGE, result, latency_seconds=1.0)
    assert scores.argument_error_count == 1


def test_escalate_scenario_scored_against_its_own_ground_truth():
    result = InvestigationResult(
        scenario_key="payments-db-latency",
        status="diagnosed",
        diagnosis=_diagnosis(recommended_action="escalate", evidence=["metrics:payments-api:latency_ms"]),
        evidence_trail=[],
        steps_used=1,
        estimated_cost_usd=0.001,
        policy_verdict="ESCALATE",
        evidence_grounded=True,
    )
    scores = score_deterministic(PAYMENTS_DB_LATENCY, result, latency_seconds=1.0)
    assert scores.policy_verdict_correct is True
    assert scores.recommended_action_exact_match is True
