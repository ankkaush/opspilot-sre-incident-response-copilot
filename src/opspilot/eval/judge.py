"""LLM-as-judge scoring — the dimensions a string comparison can't capture:
does the diagnosis actually identify the right root cause in substance (not
just share a keyword with the ground truth), is the cited evidence really
faithful to what was gathered (the RAGAS-style faithfulness idea, borrowed
as a rubric rather than imported as a dependency — see the blueprint's own
reasoning for not depending on RAGAS at this project's scale), is the
recommended action a sound response to the diagnosis, and — given what was
actually found, not just a fixed label — was escalating (or not) the right
call.

Each dimension is scored independently, never blended into one number: a
diagnosis that's completely right but recommends a slightly-off remediation
should show up as a high diagnosis score and a lower remediation score, not
get averaged into a single "pretty good" figure that hides which part
needs work.

The judge only ever sees the scenario's ground truth and the agent's actual
structured output — never the agent's tool calls or the synthetic
environment directly. Its job is comparing two texts, not re-investigating.
"""

import json

from pydantic import BaseModel, Field, ValidationError

from opspilot.agent.client import ChatFn, call_with_retries
from opspilot.agent.schemas import SubmitDiagnosisArgs
from opspilot.seed.scenarios import ScenarioSpec


class JudgeError(Exception):
    """The judge failed to return a valid, parseable judgment. Never
    silently swallowed by the runner — a broken judge call should surface,
    not quietly produce a missing score."""


class JudgeScores(BaseModel):
    diagnosis_accuracy: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Does the diagnosis correctly identify the root cause described in the ground "
            "truth, in substance?"
        ),
    )
    diagnosis_accuracy_reasoning: str

    evidence_groundedness: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Is the cited evidence plausibly real and actually supportive of the diagnosis, "
            "not fabricated or irrelevant?"
        ),
    )
    evidence_groundedness_reasoning: str

    remediation_quality: float = Field(
        ge=0.0,
        le=1.0,
        description="Given the diagnosis, is the recommended action an appropriate, proportionate response?",
    )
    remediation_quality_reasoning: str

    escalation_correct: bool = Field(
        description=(
            "Given what the diagnosis actually found, was escalating (or not escalating) "
            "the right call?"
        )
    )
    escalation_reasoning: str


_SUBMIT_JUDGMENT_TOOL = {
    "name": "submit_judgment",
    "description": "Submit your independent scores for this investigation against its ground truth.",
    "input_schema": JudgeScores.model_json_schema(),
}

_JUDGE_SYSTEM_PROMPT = (
    "You are an impartial evaluator for OpsPilot, an SRE incident-investigation agent. You "
    "will be given a scenario's ground truth and the agent's actual diagnosis for that "
    "scenario. Score the agent's output on each requested dimension independently — a low "
    "score on one dimension must not bias another. Be specific in your reasoning for each "
    "score. Call submit_judgment exactly once with all four scores."
)


def _build_judge_prompt(scenario: ScenarioSpec, diagnosis: SubmitDiagnosisArgs) -> str:
    return (
        f"Scenario: {scenario.title}\n\n"
        f"Ground truth root cause: {scenario.ground_truth.expected_diagnosis}\n"
        f"Ground truth expected action: {scenario.ground_truth.expected_action}\n"
        f"Ground truth expected policy verdict: {scenario.ground_truth.expected_policy_verdict}\n\n"
        f"Agent's actual diagnosis: {diagnosis.diagnosis}\n"
        f"Agent's cited evidence: {json.dumps(diagnosis.evidence)}\n"
        f"Agent's recommended action: {diagnosis.recommended_action}\n"
        f"Agent's stated confidence: {diagnosis.confidence}\n"
    )


def judge_investigation(
    chat_fn: ChatFn, scenario: ScenarioSpec, diagnosis: SubmitDiagnosisArgs
) -> JudgeScores:
    response = call_with_retries(
        chat_fn,
        messages=[{"role": "user", "content": _build_judge_prompt(scenario, diagnosis)}],
        tools=[_SUBMIT_JUDGMENT_TOOL],
        system=_JUDGE_SYSTEM_PROMPT,
    )

    tool_use_blocks = [b for b in response.content if hasattr(b, "name")]
    if not tool_use_blocks:
        raise JudgeError(f"Judge for '{scenario.key}' did not call submit_judgment.")

    try:
        return JudgeScores.model_validate(tool_use_blocks[0].input)
    except ValidationError as exc:
        raise JudgeError(f"Judge for '{scenario.key}' returned invalid scores: {exc}") from exc
