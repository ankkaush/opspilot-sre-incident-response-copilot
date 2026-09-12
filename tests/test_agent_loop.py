"""Full-graph integration tests, driven through the public `investigate()`
entrypoint (and, for the human-in-the-loop tests, `resume_investigation`
directly) with a scripted model client — no network access, no API key.
As of v0.2 Phase 1 this exercises the LangGraph state machine end to end
rather than the retired while-loop; the point of these tests hasn't
changed — a full investigation should still produce a grounded diagnosis,
and the deterministic ceilings/validation should still stop a misbehaving
run — only the thing under test's internal control flow has.
"""

from opspilot.agent.client import TransientProviderError
from opspilot.agent.graph import resume_investigation
from opspilot.agent.loop import investigate
from tests.fakes import ScriptedChatFn, text_response, tool_use_response


def _happy_path_script(action: str) -> ScriptedChatFn:
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
                    "recommended_action": action,
                },
            ),
        ]
    )


def test_gated_recommendation_pauses_for_approval_instead_of_finishing(db_session, checkout_scenario):
    """v0.2 Phase 3's core behavior change: a diagnosis this confident and
    well-grounded still doesn't execute rollback_deployment on its own —
    the graph pauses and waits for a human, exactly like a REQUIRE_APPROVAL
    verdict is supposed to mean."""
    result = investigate(
        db_session,
        checkout_scenario,
        chat_fn=_happy_path_script("rollback_deployment"),
        thread_id="test-gated-pause",
        max_steps=8,
        max_cost_usd=1.0,
    )

    assert result.status == "awaiting_approval"
    assert result.diagnosis is not None
    assert result.diagnosis.recommended_action == "rollback_deployment"
    assert result.steps_used == 3
    assert len(result.evidence_trail) == 3
    # New in v0.2 Phase 1 — the graph's hypothesize/classify_risk nodes still
    # ran (they're upstream of evaluate_policy's interrupt):
    assert result.evidence_grounded is True
    assert result.risk_tier == "medium"
    # Genuinely paused, not silently decided:
    assert result.policy_verdict is None
    assert result.remediation_result is None
    assert result.pending_approval == {
        "action_type": "rollback_deployment",
        "service": "checkout-api",
        "diagnosis": "Deployment v2.8 exhausted the DB connection pool.",
        "confidence": 0.9,
    }


def test_full_hitl_flow_approval_resumes_and_executes_exactly_once(db_session, checkout_scenario):
    thread_id = "test-hitl-approve"
    paused = investigate(
        db_session,
        checkout_scenario,
        chat_fn=_happy_path_script("rollback_deployment"),
        thread_id=thread_id,
        max_steps=8,
        max_cost_usd=1.0,
    )
    assert paused.status == "awaiting_approval"

    decision = {"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}}
    resumed = resume_investigation(
        db_session,
        checkout_scenario,
        chat_fn=ScriptedChatFn(responses=[]),  # must never be called — resuming skips gather_context
        thread_id=thread_id,
        decision=decision,
    )

    assert resumed.status == "diagnosed"
    assert resumed.policy_verdict == "REQUIRE_APPROVAL"
    assert resumed.approval_decision == decision
    assert resumed.remediation_result is not None
    assert resumed.remediation_result["action"] == "rollback_deployment"
    assert resumed.remediation_result["target_version"] == "v2.7"
    # Evidence gathered before the pause carries forward, not duplicated:
    assert len(resumed.evidence_trail) == 3


def test_full_hitl_flow_denial_resumes_without_executing(db_session, checkout_scenario):
    thread_id = "test-hitl-deny"
    paused = investigate(
        db_session,
        checkout_scenario,
        chat_fn=_happy_path_script("rollback_deployment"),
        thread_id=thread_id,
        max_steps=8,
        max_cost_usd=1.0,
    )
    assert paused.status == "awaiting_approval"

    decision = {"approved": False, "actor": "bob", "params": None}
    resumed = resume_investigation(
        db_session,
        checkout_scenario,
        chat_fn=ScriptedChatFn(responses=[]),  # must never be called — resuming skips gather_context
        thread_id=thread_id,
        decision=decision,
    )

    assert resumed.status == "diagnosed"
    assert resumed.policy_verdict == "REQUIRE_APPROVAL"
    assert resumed.approval_decision == decision
    assert resumed.remediation_result is None


def test_provider_failure_ends_the_investigation_gracefully(db_session, checkout_scenario):
    """Failure-injection, full graph: the model is unreachable for every
    call. Must end in a defined terminal state, not an unhandled
    exception."""
    always_fails = ScriptedChatFn(responses=[], default=TransientProviderError("simulated timeout"))

    result = investigate(db_session, checkout_scenario, chat_fn=always_fails, max_steps=8, max_cost_usd=1.0)

    assert result.status == "incomplete_provider_error"
    assert result.diagnosis is None


def test_full_graph_diagnoses_the_payments_scenario_as_escalate(db_session, payments_scenario):
    scripted = ScriptedChatFn(
        responses=[
            tool_use_response("t1", "get_dependency_status", {}),
            tool_use_response("t2", "get_metrics", {"metric_name": "latency_ms", "since_minutes": 60}),
            tool_use_response(
                "t3",
                "submit_diagnosis",
                {
                    "diagnosis": "Elevated payments-api latency correlates with a degraded payments "
                    "database, with no recent deployment — root cause is infrastructure-level.",
                    "evidence": ["dependency_status:postgres-payments", "metrics:latency_ms"],
                    "confidence": 0.7,
                    "recommended_action": "escalate",
                },
            ),
        ]
    )

    result = investigate(db_session, payments_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.diagnosis.recommended_action == "escalate"
    assert result.risk_tier == "none"
    assert result.evidence_grounded is True
    assert result.policy_verdict == "ESCALATE"
    assert result.remediation_result is None


def test_full_graph_auto_executes_a_low_risk_recommended_action(db_session, checkout_scenario):
    """Not a ground-truth-accurate diagnosis for this scenario — just proves
    the EXECUTE path runs end to end through the whole graph, including an
    actual (simulated) remediation tool call, when the policy table allows
    it without a human."""
    scripted = ScriptedChatFn(
        responses=[
            tool_use_response(
                "t1",
                "submit_diagnosis",
                {
                    "diagnosis": "checkout-api is in a bad in-memory state; a restart should clear it.",
                    "evidence": ["logs:connection pool exhausted"],
                    "confidence": 0.6,
                    "recommended_action": "restart_service",
                },
            )
        ]
    )

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.policy_verdict == "EXECUTE"
    assert result.remediation_result is not None
    assert result.remediation_result["action"] == "restart_service"
    assert result.remediation_result["simulated"] is True


def test_malformed_tool_arguments_are_rejected_without_crashing(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
        responses=[
            # since_minutes must be an int within [1, 10080] — this violates the schema
            tool_use_response("t1", "get_metrics", {"since_minutes": "not-a-number"}),
            tool_use_response(
                "t2",
                "submit_diagnosis",
                {
                    "diagnosis": "Recovered after a validation error and still reached a conclusion.",
                    "evidence": ["deployment:v2.8"],
                    "confidence": 0.5,
                    "recommended_action": "escalate",
                },
            ),
        ]
    )

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.evidence_trail[0].tool_name == "get_metrics"
    assert result.evidence_trail[0].error is not None
    assert result.evidence_trail[0].result is None


def test_unknown_tool_name_is_rejected_without_crashing(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
        responses=[
            tool_use_response("t1", "delete_all_production_data", {}),
            tool_use_response(
                "t2",
                "submit_diagnosis",
                {
                    "diagnosis": "Continued after an unknown tool call was rejected.",
                    "evidence": ["deployment:v2.8"],
                    "confidence": 0.4,
                    "recommended_action": "escalate",
                },
            ),
        ]
    )

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.evidence_trail[0].tool_name == "delete_all_production_data"
    assert "Unknown tool" in result.evidence_trail[0].error


def test_malformed_diagnosis_is_rejected_and_loop_continues(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
        responses=[
            # confidence out of [0, 1] range — schema violation
            tool_use_response(
                "t1",
                "submit_diagnosis",
                {
                    "diagnosis": "Too confident by far.",
                    "evidence": ["deployment:v2.8"],
                    "confidence": 5.0,
                    "recommended_action": "rollback_deployment",
                },
            ),
            tool_use_response(
                "t2",
                "submit_diagnosis",
                {
                    "diagnosis": "Corrected diagnosis with valid confidence.",
                    "evidence": ["deployment:v2.8"],
                    "confidence": 0.8,
                    # "escalate", not "rollback_deployment" — this test is about
                    # gather_context's malformed-diagnosis recovery, not the
                    # approval pause (covered separately, see the HITL tests).
                    "recommended_action": "escalate",
                },
            ),
        ]
    )

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.evidence_trail[0].error is not None
    assert result.diagnosis.confidence == 0.8


def test_step_ceiling_stops_a_loop_that_never_concludes(db_session, checkout_scenario):
    scripted = ScriptedChatFn(responses=[], default=text_response("still investigating..."))

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=3, max_cost_usd=1.0)

    assert result.status == "incomplete_step_ceiling"
    assert result.diagnosis is None
    assert result.steps_used == 3
    assert len(scripted.calls) == 3


def test_cost_ceiling_stops_the_loop_before_the_step_ceiling(db_session, checkout_scenario):
    expensive = text_response("thinking...", in_tok=10_000, out_tok=1_000_000)
    scripted = ScriptedChatFn(responses=[], default=expensive)

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=0.01)

    assert result.status == "incomplete_cost_ceiling"
    assert result.diagnosis is None
    assert result.steps_used < 8
