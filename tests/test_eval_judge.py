"""Judge tests use the same scripted fake as everywhere else in this
project — the judge is just another single-turn model call behind the
ChatFn protocol, so no new test infrastructure is needed."""

import pytest

from opspilot.agent.schemas import SubmitDiagnosisArgs
from opspilot.eval.judge import JudgeError, judge_investigation
from opspilot.seed.scenarios import CHECKOUT_DEPLOY_OUTAGE
from tests.fakes import ScriptedChatFn, text_response, tool_use_response

_VALID_JUDGMENT = {
    "diagnosis_accuracy": 0.95,
    "diagnosis_accuracy_reasoning": "Correctly identifies the pool-size reduction as root cause.",
    "evidence_groundedness": 0.9,
    "evidence_groundedness_reasoning": "Cited evidence matches what would plausibly be gathered.",
    "remediation_quality": 0.9,
    "remediation_quality_reasoning": "Rollback is the appropriate response to a bad deploy.",
    "escalation_correct": True,
    "escalation_reasoning": "Not escalating is correct — this is within the agent's remediation scope.",
}


def _diagnosis() -> SubmitDiagnosisArgs:
    return SubmitDiagnosisArgs(
        diagnosis="Deployment v2.8 reduced the DB connection pool, causing exhaustion.",
        evidence=["deployment:checkout-api:v2.8"],
        confidence=0.9,
        recommended_action="rollback_deployment",
    )


def test_judge_parses_a_valid_judgment():
    scripted = ScriptedChatFn(
        responses=[tool_use_response("j1", "submit_judgment", _VALID_JUDGMENT)]
    )

    scores = judge_investigation(scripted, CHECKOUT_DEPLOY_OUTAGE, _diagnosis())

    assert scores.diagnosis_accuracy == 0.95
    assert scores.escalation_correct is True
    assert scripted.calls[0]["tools"][0]["name"] == "submit_judgment"


def test_judge_raises_when_no_tool_call_is_made():
    scripted = ScriptedChatFn(responses=[text_response("I'm not sure how to score this.")])

    with pytest.raises(JudgeError):
        judge_investigation(scripted, CHECKOUT_DEPLOY_OUTAGE, _diagnosis())


def test_judge_raises_on_malformed_scores():
    malformed = {**_VALID_JUDGMENT, "diagnosis_accuracy": 5.0}  # out of [0, 1]
    scripted = ScriptedChatFn(responses=[tool_use_response("j1", "submit_judgment", malformed)])

    with pytest.raises(JudgeError):
        judge_investigation(scripted, CHECKOUT_DEPLOY_OUTAGE, _diagnosis())


def test_judge_prompt_includes_ground_truth_and_actual_diagnosis():
    scripted = ScriptedChatFn(
        responses=[tool_use_response("j1", "submit_judgment", _VALID_JUDGMENT)]
    )
    diagnosis = _diagnosis()

    judge_investigation(scripted, CHECKOUT_DEPLOY_OUTAGE, diagnosis)

    prompt = scripted.calls[0]["messages"][0]["content"]
    assert CHECKOUT_DEPLOY_OUTAGE.ground_truth.expected_diagnosis in prompt
    assert diagnosis.diagnosis in prompt
