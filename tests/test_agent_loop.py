"""Loop-level tests using a scripted model client — no network access, no API
key. These exercise exactly what v0.1 Phase 2's "done when" and testing
requirements ask for: a full investigation producing a grounded diagnosis,
and the deterministic ceilings/validation that stop a misbehaving loop.
"""

from opspilot.agent.loop import investigate
from tests.fakes import ScriptedChatFn, text_response, tool_use_response


def test_happy_path_produces_grounded_diagnosis(db_session, checkout_scenario):
    scripted = ScriptedChatFn(
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

    result = investigate(db_session, checkout_scenario, chat_fn=scripted, max_steps=8, max_cost_usd=1.0)

    assert result.status == "diagnosed"
    assert result.diagnosis is not None
    assert result.diagnosis.recommended_action == "rollback_deployment"
    assert result.steps_used == 3
    assert len(result.evidence_trail) == 3
    assert result.evidence_trail[0].tool_name == "get_recent_deployments"
    assert result.evidence_trail[0].error is None
    assert result.evidence_trail[2].tool_name == "submit_diagnosis"


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
                    "recommended_action": "rollback_deployment",
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
