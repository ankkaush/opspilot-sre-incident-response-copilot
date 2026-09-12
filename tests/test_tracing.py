"""Tracing tests run against the real Langfuse client, not a fake — and
that's deliberate. The test environment never sets LANGFUSE_PUBLIC_KEY/
LANGFUSE_SECRET_KEY, so every call here exercises the SDK's own no-op path
(see the module docstring in opspilot.agent.tracing): if that path ever
raised or silently changed agent behavior, these tests would catch it
without needing a real Langfuse account.
"""

from opspilot.agent.schemas import SubmitDiagnosisArgs
from opspilot.agent.tracing import (
    get_langfuse_client,
    get_trace_url,
    to_jsonable,
    trace_generation,
    trace_investigation,
    trace_node,
    trace_tool_call,
)


def test_get_langfuse_client_is_a_singleton():
    assert get_langfuse_client() is get_langfuse_client()


def test_redaction_masks_secret_shaped_keys():
    client = get_langfuse_client()
    masked = client._mask(
        data={"api_key": "sk-ant-should-not-appear", "note": "safe", "nested": {"password": "hunter2"}}
    )
    assert masked["api_key"] == "[REDACTED]"
    assert masked["nested"]["password"] == "[REDACTED]"
    assert masked["note"] == "safe"


def test_redaction_is_case_insensitive_on_keys():
    client = get_langfuse_client()
    masked = client._mask(data={"API_KEY": "should-be-redacted", "Authorization": "also-redacted"})
    assert masked["API_KEY"] == "[REDACTED]"
    assert masked["Authorization"] == "[REDACTED]"


def test_to_jsonable_converts_pydantic_models():
    diagnosis = SubmitDiagnosisArgs(
        diagnosis="A sufficiently long diagnosis string.",
        evidence=["deployment:v2.8"],
        confidence=0.8,
        recommended_action="rollback_deployment",
    )
    result = to_jsonable({"diagnosis": diagnosis, "items": [diagnosis]})
    assert result["diagnosis"]["recommended_action"] == "rollback_deployment"
    assert result["items"][0]["confidence"] == 0.8


def test_to_jsonable_passes_through_plain_values():
    assert to_jsonable({"a": 1, "b": [1, 2, {"c": None}]}) == {"a": 1, "b": [1, 2, {"c": None}]}


def test_trace_investigation_yields_without_raising_when_unconfigured():
    with trace_investigation(
        scenario_key="checkout-deploy-outage", thread_id="test-thread", prompt_version="0.3.0"
    ) as trace_id:
        # Disabled client: no exception, and a trace id may or may not be
        # produced depending on the SDK's own no-op behavior — either way
        # this must never raise, since tracing must never affect whether an
        # investigation itself succeeds.
        assert trace_id is None or isinstance(trace_id, str)


def test_trace_node_yields_a_span_without_raising():
    with trace_node("hypothesize", input={"diagnosis": None}) as span:
        span.update(output={"evidence_grounded": True})


def test_trace_generation_yields_without_raising():
    messages = [{"role": "user", "content": "hi"}]
    with trace_generation(model="claude-sonnet-5", messages=messages, tool_count=5) as gen:
        gen.update(output=[{"type": "text", "text": "ok"}], usage_details={"input": 10, "output": 5})


def test_trace_tool_call_yields_without_raising():
    with trace_tool_call(tool_name="get_metrics", arguments={"since_minutes": 60}) as span:
        span.update(output=[{"metric_name": "error_rate", "value": 1.2}])


def test_get_trace_url_returns_none_for_none_input():
    assert get_trace_url(None) is None
