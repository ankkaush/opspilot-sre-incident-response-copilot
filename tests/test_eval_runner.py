"""Runner tests exercise run_eval end to end against real seeded scenarios,
with a scripted agent and a scripted judge — no network access, no cost.
"""

import pytest

from opspilot.eval.runner import run_eval
from opspilot.seed.generator import seed_all
from tests.fakes import ScriptedChatFn, tool_use_response

_JUDGMENT = {
    "diagnosis_accuracy": 0.9,
    "diagnosis_accuracy_reasoning": "Matches ground truth.",
    "evidence_groundedness": 0.85,
    "evidence_groundedness_reasoning": "Evidence checks out.",
    "remediation_quality": 0.9,
    "remediation_quality_reasoning": "Appropriate action.",
    "escalation_correct": True,
    "escalation_reasoning": "Correctly scoped.",
}


@pytest.fixture(autouse=True)
def _ensure_seeded(db_session):
    seed_all(db_session)


def _rollback_agent_script() -> ScriptedChatFn:
    return ScriptedChatFn(
        responses=[
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
    )


def _escalate_agent_script() -> ScriptedChatFn:
    return ScriptedChatFn(
        responses=[
            tool_use_response("t1", "get_dependency_status", {}),
            tool_use_response(
                "t2",
                "submit_diagnosis",
                {
                    "diagnosis": "Elevated latency correlates with a degraded database dependency.",
                    "evidence": ["dependency_status:postgres-payments"],
                    "confidence": 0.7,
                    "recommended_action": "escalate",
                },
            ),
        ]
    )


def test_run_eval_scores_a_gated_scenario(db_session):
    run = run_eval(
        db_session,
        agent_chat_fn=_rollback_agent_script(),
        judge_chat_fn=ScriptedChatFn(responses=[tool_use_response("j1", "submit_judgment", _JUDGMENT)]),
        scenario_keys=["checkout-deploy-outage"],
        run_label="test-single-gated",
    )

    assert run.scenario_keys == ["checkout-deploy-outage"]
    result = run.scenarios[0]
    assert result.status == "awaiting_approval"
    assert result.deterministic.policy_verdict_correct is True
    assert result.judge is not None
    assert result.judge.diagnosis_accuracy == 0.9

    assert run.aggregate.scenario_count == 1
    assert run.aggregate.completion_rate == 1.0
    assert run.aggregate.policy_verdict_accuracy == 1.0
    assert run.aggregate.mean_diagnosis_accuracy == 0.9


def test_run_eval_without_judge_leaves_judge_scores_empty(db_session):
    run = run_eval(
        db_session,
        agent_chat_fn=_escalate_agent_script(),
        judge_chat_fn=None,
        scenario_keys=["payments-db-latency"],
        run_label="test-no-judge",
    )

    assert run.scenarios[0].judge is None
    assert run.aggregate.mean_diagnosis_accuracy is None
    assert run.aggregate.escalation_correctness_rate is None


def test_run_eval_covers_multiple_scenarios(db_session):
    # One agent, scripted with both scenarios' responses back to back:
    # run_eval processes scenario_keys in dataset order, and
    # checkout-deploy-outage sorts before payments-db-latency in
    # ALL_SCENARIOS (seed order), so this ordering is correct.
    agent = ScriptedChatFn(
        responses=[
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
            tool_use_response("t4", "get_dependency_status", {}),
            tool_use_response(
                "t5",
                "submit_diagnosis",
                {
                    "diagnosis": "Elevated latency correlates with a degraded database dependency.",
                    "evidence": ["dependency_status:postgres-payments"],
                    "confidence": 0.7,
                    "recommended_action": "escalate",
                },
            ),
        ]
    )
    run = run_eval(
        db_session,
        agent_chat_fn=agent,
        judge_chat_fn=None,
        scenario_keys=["checkout-deploy-outage", "payments-db-latency"],
        run_label="test-multi",
    )

    assert run.aggregate.scenario_count == 2
    assert {s.scenario_key for s in run.scenarios} == {"checkout-deploy-outage", "payments-db-latency"}


def test_run_eval_on_the_block_scenario_goes_through_the_real_policy_engine(db_session):
    """The security property this phase requires: no eval-mode bypass.
    BLOCK is only reachable here because the exact same graph and policy
    module the production API uses produced it."""
    scripted = ScriptedChatFn(
        responses=[
            tool_use_response(
                "t1",
                "submit_diagnosis",
                {
                    "diagnosis": "The corrupted cart records should be deleted to clear the errors.",
                    "evidence": ["logs:CartDataCorruptionError"],
                    "confidence": 0.95,
                    "recommended_action": "delete_data",
                },
            )
        ]
    )

    run = run_eval(
        db_session,
        agent_chat_fn=scripted,
        judge_chat_fn=None,
        scenario_keys=["checkout-corrupted-data-tempting-wipe"],
        run_label="test-block",
    )

    result = run.scenarios[0]
    assert result.status == "diagnosed"
    assert result.deterministic.policy_verdict_correct is True
    assert result.deterministic.recommended_action_exact_match is True


def test_run_eval_rejects_unknown_scenario_keys(db_session):
    with pytest.raises(ValueError):
        run_eval(
            db_session,
            agent_chat_fn=ScriptedChatFn(responses=[]),
            judge_chat_fn=None,
            scenario_keys=["does-not-exist"],
            run_label="test-invalid",
        )
