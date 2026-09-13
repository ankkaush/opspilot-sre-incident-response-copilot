"""v0.4 Phase 2's measured-impact requirement: a repeat-pattern eval
scenario shows a reproducible improvement with memory enabled vs. disabled,
using the v0.3 eval harness (opspilot.eval.runner) unchanged.

"Repeat pattern" here means priming service_memory with the exact,
ground-truth-matching confirmed diagnosis for an existing golden scenario —
service-scoped memory of "this exact pattern was already seen and
confirmed" — rather than adding brand-new fixture scenarios to the golden
dataset; the golden dataset's scenarios already carry checkable ground
truth (v0.3 Phase 1), and priming one of them with its own confirmed answer
is a faithful, minimal way to construct "this happened before."

The comparison is scripted, not live: a `MemoryAwareChatFn` deterministically
behaves differently depending on whether the system prompt it receives
carries the "Prior related incidents" memory block — fewer confirmatory
tool calls when a hint is present, a full evidence sweep when it isn't —
both converging on the same, correct diagnosis. This proves the retrieval
mechanism actually changes what the agent does (real prompt, real graph,
real harness), reproducibly and at zero cost, rather than trusting live
model variance between two paid runs to show a difference.
"""

import datetime as dt

import pytest

from opspilot.agent.client import ModelResponse
from opspilot.agent.schemas import InvestigationResult, SubmitDiagnosisArgs
from opspilot.eval.runner import run_eval
from opspilot.memory import write_confirmed_memory
from opspilot.models import Incident
from opspilot.seed.generator import seed_all
from tests.fakes import tool_use_response

SCENARIO_KEY = "checkout-deploy-outage"

_DIAGNOSIS_ARGS = {
    "diagnosis": (
        "Deployment v2.8 reduced checkout-api's DB connection pool from 50 to 5 connections, "
        "causing connection exhaustion under normal traffic."
    ),
    "evidence": ["deployment:checkout-api:v2.8", "logs:checkout-api:connection pool exhausted"],
    "confidence": 0.95,
    "recommended_action": "rollback_deployment",
}

_WITH_MEMORY_SCRIPT = [
    tool_use_response("t1", "get_recent_deployments", {"since_minutes": 60}),
    tool_use_response("t2", "submit_diagnosis", _DIAGNOSIS_ARGS),
]

_WITHOUT_MEMORY_SCRIPT = [
    tool_use_response("t1", "get_recent_deployments", {"since_minutes": 60}),
    tool_use_response("t2", "get_metrics", {"metric_name": "error_rate", "since_minutes": 60}),
    tool_use_response("t3", "get_logs", {"level": "error", "since_minutes": 60}),
    tool_use_response("t4", "get_dependency_status", {}),
    tool_use_response("t5", "submit_diagnosis", _DIAGNOSIS_ARGS),
]

_MEMORY_MARKER = "Prior related incidents"


class MemoryAwareChatFn:
    """A deterministic stand-in for "a model that converges faster when a
    prior confirmed diagnosis is already hinted at." Picks one of two fixed
    scripts based solely on whether `system` carries the memory block —
    never on call count or any other side channel — so the comparison this
    test makes is entirely attributable to opspilot.memory's retrieval
    actually reaching the prompt."""

    def __init__(self):
        self.calls: list[dict] = []
        self._script: list[ModelResponse] | None = None

    def __call__(self, *, messages: list[dict], tools: list[dict], system: str) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if self._script is None:
            self._script = list(
                _WITH_MEMORY_SCRIPT if _MEMORY_MARKER in system else _WITHOUT_MEMORY_SCRIPT
            )
        return self._script.pop(0)


@pytest.fixture(autouse=True)
def _ensure_seeded(db_session):
    seed_all(db_session)


def _prime_memory_matching_ground_truth(db_session, checkout_scenario) -> None:
    priming_incident = Incident(
        scenario_id=checkout_scenario.id, status="open", created_at=dt.datetime.now(dt.UTC)
    )
    db_session.add(priming_incident)
    db_session.flush()

    result = InvestigationResult(
        scenario_key=SCENARIO_KEY,
        status="diagnosed",
        diagnosis=SubmitDiagnosisArgs(**_DIAGNOSIS_ARGS),
        steps_used=4,
        estimated_cost_usd=0.02,
        policy_verdict="REQUIRE_APPROVAL",
        approval_decision={"approved": True, "actor": "alice"},
    )
    write_confirmed_memory(
        db_session,
        service_id=checkout_scenario.service_id,
        incident_id=priming_incident.id,
        result=result,
    )
    db_session.commit()


def test_memory_enabled_reaches_diagnosis_in_fewer_steps_than_disabled(
    db_session, checkout_scenario
):
    _prime_memory_matching_ground_truth(db_session, checkout_scenario)

    run_with_memory = run_eval(
        db_session,
        agent_chat_fn=MemoryAwareChatFn(),
        judge_chat_fn=None,
        scenario_keys=[SCENARIO_KEY],
        run_label="memory-on",
        memory_enabled=True,
    )
    run_without_memory = run_eval(
        db_session,
        agent_chat_fn=MemoryAwareChatFn(),
        judge_chat_fn=None,
        scenario_keys=[SCENARIO_KEY],
        run_label="memory-off",
        memory_enabled=False,
    )

    # Both runs reach the correct diagnosis — memory changes efficiency,
    # not correctness.
    assert run_with_memory.aggregate.completion_rate == 1.0
    assert run_without_memory.aggregate.completion_rate == 1.0
    assert run_with_memory.aggregate.recommended_action_exact_match_rate == 1.0
    assert run_without_memory.aggregate.recommended_action_exact_match_rate == 1.0
    assert run_with_memory.aggregate.policy_verdict_accuracy == 1.0
    assert run_without_memory.aggregate.policy_verdict_accuracy == 1.0

    # The measurable, reproducible improvement itself.
    assert run_with_memory.aggregate.mean_steps_used < run_without_memory.aggregate.mean_steps_used
    assert run_with_memory.aggregate.mean_steps_used == 2.0
    assert run_without_memory.aggregate.mean_steps_used == 5.0


def test_memory_disabled_never_surfaces_memory_even_when_primed(db_session, checkout_scenario):
    """Sanity check on the harness's own on/off lever: without it, this
    test would be vacuous (both runs would behave identically for reasons
    that have nothing to do with memory)."""
    _prime_memory_matching_ground_truth(db_session, checkout_scenario)

    chat_fn = MemoryAwareChatFn()
    run_eval(
        db_session,
        agent_chat_fn=chat_fn,
        judge_chat_fn=None,
        scenario_keys=[SCENARIO_KEY],
        run_label="memory-off-sanity",
        memory_enabled=False,
    )
    assert all(_MEMORY_MARKER not in call["system"] for call in chat_fn.calls)
