"""Tracing tests run against the real Langfuse client, not a fake — and
that's deliberate. Whether *this developer's* local `.env` happens to carry
real Langfuse credentials or not, every call here must behave the same way,
since a missing/present Langfuse account must never change agent behavior
(see the module docstring in opspilot.agent.tracing) — only whether anyone
can see it run.

The redaction tests call `_redact` directly rather than going through
`get_langfuse_client()._mask` — that private SDK attribute is only ever
populated once the client has been constructed with real credentials, so
asserting against it silently depended on whichever machine ran the suite
happening to have LANGFUSE_PUBLIC_KEY/SECRET_KEY configured locally. `_redact`
is the actual policy under test either way; testing it directly is what
makes these tests genuinely environment-independent instead of accidentally
passing in dev and failing in CI.
"""

from langfuse import Langfuse

from opspilot.agent import tracing as tracing_module
from opspilot.agent.schemas import SubmitDiagnosisArgs
from opspilot.agent.tracing import (
    _redact,
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
    masked = _redact(
        data={"api_key": "sk-ant-should-not-appear", "note": "safe", "nested": {"password": "hunter2"}}
    )
    assert masked["api_key"] == "[REDACTED]"
    assert masked["nested"]["password"] == "[REDACTED]"
    assert masked["note"] == "safe"


def test_redaction_is_case_insensitive_on_keys():
    masked = _redact(data={"API_KEY": "should-be-redacted", "Authorization": "also-redacted"})
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


def test_get_trace_url_no_ops_when_langfuse_is_unconfigured(monkeypatch):
    """A non-None trace_id does NOT imply Langfuse is configured —
    OpenTelemetry assigns ids locally regardless of whether there's
    anywhere to export them to (see get_trace_url's own docstring). This
    is what actually exercises that path: an explicitly credential-less
    client, independent of whatever this machine's own .env happens to
    contain, so the test can't accidentally pass only because a real
    account is configured locally."""
    unconfigured_client = Langfuse(public_key=None, secret_key=None, host=None)
    monkeypatch.setattr(tracing_module, "get_langfuse_client", lambda: unconfigured_client)

    assert get_trace_url("some-trace-id-1234") is None
