"""Langfuse tracing — every node, model call, and tool call gets its own
observation, all nested under one trace per investigation attempt.

Tracing is observability, never control: the Langfuse client (imported,
not written here) already no-ops gracefully when `LANGFUSE_PUBLIC_KEY`/
`LANGFUSE_SECRET_KEY` aren't configured — it logs a warning once and every
call after that is a cheap no-op, never an exception. That property is load
-bearing for this module's whole design: nothing here needs its own
"is tracing enabled" branches, because a missing Langfuse account must never
change whether or how the agent runs, only whether anyone can see it run.

`GRAPH_VERSION` is bumped whenever the graph's node/edge structure changes;
`opspilot.agent.support` carries the analogous `PROMPT_VERSION` for the
system prompt text. Both are attached to every trace so a run can be
filtered or compared by exactly which version of the graph and prompt
produced it — the blueprint's "tagged with prompt/graph version"
requirement, and the concrete answer to "why did this scorecard get worse
after that change."
"""

from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from langfuse import Langfuse
from pydantic import BaseModel

from opspilot.config import get_settings

GRAPH_VERSION = "0.3.0"

_REDACT_KEYS = {"api_key", "authorization", "x-api-key", "password", "secret", "token"}


def _redact(*, data: Any, **_kwargs: Any) -> Any:
    """Langfuse calls this on every payload before it leaves the process —
    the "explicit redaction pass" the blueprint requires for this phase.
    Key-name-based, matching the same policy as `opspilot.logging_config`,
    applied recursively. There's no synthetic PII to redact by value (the
    seed dataset never invents customer names/emails — see the golden
    dataset's own scenarios), so key-based redaction of secret-shaped
    fields is the whole policy."""
    if isinstance(data, dict):
        return {
            key: ("[REDACTED]" if key.lower() in _REDACT_KEYS else _redact(data=value))
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_redact(data=item) for item in data]
    return data


def to_jsonable(value: Any) -> Any:
    """Recursively converts Pydantic models (ToolCallRecord,
    SubmitDiagnosisArgs, ...) to plain dicts before anything reaches
    Langfuse — explicit rather than relying on the SDK's own serializer to
    guess how to handle an arbitrary object."""
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


@lru_cache
def get_langfuse_client() -> Langfuse:
    settings = get_settings()
    return Langfuse(
        public_key=settings.langfuse_public_key or None,
        secret_key=settings.langfuse_secret_key or None,
        host=settings.langfuse_base_url or None,
        mask=_redact,
    )


def get_trace_url(trace_id: str | None) -> str | None:
    if trace_id is None:
        return None
    return get_langfuse_client().get_trace_url(trace_id=trace_id)


@contextmanager
def trace_investigation(*, scenario_key: str, thread_id: str, prompt_version: str):
    """The top-level span for one run_investigation/resume_investigation
    call. Yields the trace id (None if tracing is disabled) so callers can
    persist it — that id is what "eval runs linked to their traces by id"
    and the dashboard's trace drill-through both key off."""
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name="investigation",
        as_type="span",
        input={"scenario_key": scenario_key},
        metadata={
            "graph_version": GRAPH_VERSION,
            "prompt_version": prompt_version,
            "thread_id": thread_id,
        },
    ):
        trace_id = client.get_current_trace_id()
        try:
            yield trace_id
        finally:
            client.flush()


@contextmanager
def trace_node(name: str, *, input: dict | None = None):
    """Generic wrapper for the graph's non-looping nodes (hypothesize,
    classify_risk, evaluate_policy, decide) — see graph.py's build_graph,
    which applies this uniformly rather than each node instrumenting
    itself. gather_context is the exception: its internal model-call and
    tool-call loop needs finer-grained spans than a single wrap can give
    it, so it uses trace_generation/trace_tool_call directly instead."""
    client = get_langfuse_client()
    with client.start_as_current_observation(name=name, as_type="span", input=input) as span:
        yield span


@contextmanager
def trace_generation(*, model: str, messages: list[dict], tool_count: int):
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name="gather_context.model_call",
        as_type="generation",
        model=model,
        input={"messages": messages, "tool_count": tool_count},
    ) as generation:
        yield generation


@contextmanager
def trace_tool_call(*, tool_name: str, arguments: dict):
    client = get_langfuse_client()
    with client.start_as_current_observation(
        name=f"tool:{tool_name}", as_type="tool", input=arguments
    ) as span:
        yield span
