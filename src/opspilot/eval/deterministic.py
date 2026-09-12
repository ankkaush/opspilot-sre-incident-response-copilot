"""Deterministic evaluation checks — scored by code, no model call, no
judgment. These are the dimensions that genuinely have a right answer:
which tools *should* have been called, whether the policy verdict matches
the scenario's ground truth, whether any tool call errored. Everything
here is a pure function of a `ScenarioSpec` and an `InvestigationResult` —
no network access, no cost, reproducible forever.

`opspilot.eval.judge` picks up the dimensions that don't have a clean
right answer a string comparison can capture.
"""

from pydantic import BaseModel

from opspilot.agent.schemas import InvestigationResult
from opspilot.seed.scenarios import ScenarioSpec

# Which tool a given expected_evidence category implies should have been
# called. Scenarios never cite a "runbook:" category in expected_evidence
# (get_runbook is a lookup aid, not itself evidence of the root cause), so
# it's included here for completeness but never actually exercised by the
# current dataset.
_EVIDENCE_CATEGORY_TOOL = {
    "deployment": "get_recent_deployments",
    "metrics": "get_metrics",
    "logs": "get_logs",
    "dependency_status": "get_dependency_status",
    "runbook": "get_runbook",
}


class DeterministicScores(BaseModel):
    reached_diagnosis: bool

    # None for every field below when reached_diagnosis is False — there's
    # nothing to compare against a diagnosis that was never reached.
    policy_verdict_correct: bool | None = None
    recommended_action_exact_match: bool | None = None
    # The Phase 1 keyword-matching heuristic from opspilot.agent.nodes,
    # carried straight through from the InvestigationResult — a free,
    # cheap second opinion to compare against the judge's semantic
    # evidence_groundedness score.
    evidence_grounded_heuristic: bool | None = None

    tool_selection_score: float  # fraction of expected tool categories actually called
    unnecessary_tool_call_count: int
    argument_error_count: int

    steps_used: int
    estimated_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    latency_seconds: float


def _effective_policy_verdict(result: InvestigationResult) -> str | None:
    """awaiting_approval IS the correct outcome for a REQUIRE_APPROVAL
    scenario at eval time — the harness doesn't need to actually decide
    the approval to know the policy engine routed it correctly."""
    if result.status == "diagnosed":
        return result.policy_verdict
    if result.status == "awaiting_approval":
        return "REQUIRE_APPROVAL"
    return None


def score_deterministic(
    scenario: ScenarioSpec, result: InvestigationResult, *, latency_seconds: float
) -> DeterministicScores:
    reached = result.status in ("diagnosed", "awaiting_approval")

    expected_tools = {
        _EVIDENCE_CATEGORY_TOOL[category]
        for evidence in scenario.ground_truth.expected_evidence
        if (category := evidence.split(":", 1)[0]) in _EVIDENCE_CATEGORY_TOOL
    }
    called_tools = {r.tool_name for r in result.evidence_trail if r.tool_name != "submit_diagnosis"}
    tool_selection_score = (
        round(len(expected_tools & called_tools) / len(expected_tools), 3) if expected_tools else 1.0
    )
    unnecessary_tool_calls = len(called_tools - expected_tools)
    argument_errors = sum(1 for r in result.evidence_trail if r.error is not None)

    policy_verdict_correct = None
    recommended_action_exact_match = None
    if reached:
        policy_verdict_correct = (
            _effective_policy_verdict(result) == scenario.ground_truth.expected_policy_verdict
        )
        if result.diagnosis is not None:
            recommended_action_exact_match = (
                result.diagnosis.recommended_action == scenario.ground_truth.expected_action
            )

    return DeterministicScores(
        reached_diagnosis=reached,
        policy_verdict_correct=policy_verdict_correct,
        recommended_action_exact_match=recommended_action_exact_match,
        evidence_grounded_heuristic=result.evidence_grounded,
        tool_selection_score=tool_selection_score,
        unnecessary_tool_call_count=unnecessary_tool_calls,
        argument_error_count=argument_errors,
        steps_used=result.steps_used,
        estimated_cost_usd=result.estimated_cost_usd,
        total_input_tokens=result.total_input_tokens,
        total_output_tokens=result.total_output_tokens,
        latency_seconds=round(latency_seconds, 3),
    )
