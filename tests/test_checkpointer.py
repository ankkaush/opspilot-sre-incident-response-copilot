"""v0.5 Phase 1: durable, Postgres-backed graph checkpointing.

The kill-and-resume tests are the phase's actual "done when" — killing the
process at any node boundary and restarting must always resume from that
boundary, never from the start. "Killing the process" is simulated by
discarding a `build_graph(...)` call's checkpointer/compiled-graph objects
entirely and building fresh ones (via `new_postgres_checkpointer`, which
deliberately bypasses the cached production singleton — see its docstring)
before resuming: reusing the same live objects across the "before" and
"after" halves would prove nothing about durability, since even an
in-memory checkpointer passes that trivial version of the test.
"""

import pytest
from sqlalchemy import inspect as sa_inspect

from opspilot.agent.checkpointer import (
    _psycopg_conn_string,
    get_postgres_checkpointer,
    new_postgres_checkpointer,
)
from opspilot.agent.graph import build_graph
from opspilot.agent.loop import get_chat_fn
from opspilot.agent.state import GraphState, NodeDeps, initial_state
from opspilot.agent.tracing import _REDACT_KEYS
from opspilot.db import engine
from opspilot.main import app
from tests.fakes import ScriptedChatFn, tool_use_response

NODE_NAMES = ["gather_context", "hypothesize", "classify_risk", "evaluate_policy", "decide"]


@pytest.fixture
def make_checkpointer():
    """Wraps `new_postgres_checkpointer` and closes every pool it hands out
    at teardown — each one opens a handful of background worker threads,
    and leaving many of them to a garbage-collector/interpreter-shutdown
    finalizer (rather than an explicit close) is what produces noisy
    "cannot join thread" warnings in a test run that creates this many
    short-lived pools."""
    created = []

    def _make():
        saver = new_postgres_checkpointer()
        created.append(saver)
        return saver

    yield _make
    for saver in created:
        saver.conn.close()

# recommended_action="restart_service" verdicts EXECUTE and auto-runs with
# no human in the loop — chosen deliberately so evaluate_policy's own
# dynamic interrupt() never fires, letting the test isolate each of the
# five *static* node boundaries independently, "decide" included.
_EXECUTE_SCRIPT = [
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


def test_conn_string_conversion_strips_sqlalchemy_driver_qualifier():
    assert _psycopg_conn_string("postgresql+psycopg://u:p@host:5432/db") == (
        "postgresql://u:p@host:5432/db"
    )


def test_get_postgres_checkpointer_is_a_singleton():
    assert get_postgres_checkpointer() is get_postgres_checkpointer()


def test_new_postgres_checkpointer_is_independent_of_the_singleton(make_checkpointer):
    assert make_checkpointer() is not get_postgres_checkpointer()


def test_setup_creates_the_expected_tables():
    get_postgres_checkpointer()  # ensures setup() has run at least once
    tables = set(sa_inspect(engine).get_table_names())
    assert {"checkpoints", "checkpoint_writes", "checkpoint_blobs", "checkpoint_migrations"} <= tables


def test_graph_state_schema_has_no_secret_shaped_fields():
    """Structural guarantee, not an active redaction pass: GraphState is
    the only thing ever checkpointed (NodeDeps — the DB session, the
    chat_fn — is closed over, never serialized), and none of its field
    names match what opspilot.agent.tracing treats as secret-shaped. See
    opspilot.agent.checkpointer's module docstring."""
    field_names = {name.lower() for name in GraphState.__annotations__}
    assert not (field_names & _REDACT_KEYS)


def _collect_dict_keys(obj) -> set[str]:
    """Recursively collects every dict key in a nested structure, lower-
    cased — mirroring opspilot.agent.tracing._redact's own key-matching
    logic exactly, so this checks the same thing Langfuse's redaction pass
    checks. Deliberately keys only, never substrings of values: a field
    legitimately named `total_input_tokens` should never fail a check for
    the word "token" the way a naive text search would."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(str(key).lower())
            keys |= _collect_dict_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            keys |= _collect_dict_keys(item)
    return keys


def test_persisted_checkpoint_payload_has_no_secret_shaped_keys(
    db_session, checkout_scenario, make_checkpointer
):
    """Same guarantee, verified end to end against a real persisted row
    rather than just the schema — an investigation actually runs, and the
    raw checkpoint payload it leaves behind is inspected directly."""
    checkpointer = make_checkpointer()
    thread_id = "checkpoint-secret-scan"
    deps = NodeDeps(
        session=db_session,
        scenario=checkout_scenario,
        chat_fn=ScriptedChatFn(responses=list(_EXECUTE_SCRIPT)),
        max_steps=8,
        max_cost_usd=1.0,
        thread_id=thread_id,
    )
    compiled = build_graph(deps, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}
    compiled.invoke(initial_state(), config=config)

    checkpoint_tuple = checkpointer.get_tuple(config)
    assert checkpoint_tuple is not None
    payload_keys = _collect_dict_keys(checkpoint_tuple.checkpoint)
    assert not (payload_keys & _REDACT_KEYS)


@pytest.mark.parametrize("kill_at", NODE_NAMES)
def test_kill_and_resume_at_each_node_boundary(
    db_session, checkout_scenario, kill_at, make_checkpointer
):
    shared_chat_fn = ScriptedChatFn(responses=list(_EXECUTE_SCRIPT))
    thread_id = f"kill-resume-{kill_at}"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}

    # "Process 1": runs up to (not including) kill_at, then dies.
    deps_1 = NodeDeps(
        session=db_session,
        scenario=checkout_scenario,
        chat_fn=shared_chat_fn,
        max_steps=8,
        max_cost_usd=1.0,
        thread_id=thread_id,
    )
    compiled_1 = build_graph(
        deps_1, checkpointer=make_checkpointer(), interrupt_before=[kill_at]
    )
    partial_state = compiled_1.invoke(initial_state(), config=config)

    # The boundary actually paused where expected, not somewhere else —
    # otherwise the rest of this test would prove nothing.
    assert partial_state.get("status") is None

    # "Process 2": brand-new checkpointer, brand-new compiled graph — a
    # from-scratch attach to the same durable Postgres store, no static
    # breakpoint this time.
    deps_2 = NodeDeps(
        session=db_session,
        scenario=checkout_scenario,
        chat_fn=shared_chat_fn,
        max_steps=8,
        max_cost_usd=1.0,
        thread_id=thread_id,
    )
    compiled_2 = build_graph(deps_2, checkpointer=make_checkpointer())
    final_state = compiled_2.invoke(None, config=config)

    assert final_state["status"] == "diagnosed"
    assert final_state["policy_verdict"] == "EXECUTE"
    assert final_state["remediation_result"]["action"] == "restart_service"
    # gather_context's one model call happens exactly once no matter which
    # boundary the process "died" at — the concrete proof the resume
    # continued from the persisted checkpoint rather than restarting the
    # investigation from scratch (a from-scratch restart would call the
    # model a second time, and ScriptedChatFn would raise on the second
    # call anyway since it only has one scripted response).
    assert len(shared_chat_fn.calls) == 1


HAPPY_PATH_SCRIPT = [
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


def test_production_approval_resume_survives_a_simulated_process_restart(client, auth_headers):
    """The same guarantee as the kill-and-resume harness above, but proven
    through the real production path (routers/incidents.py's run/approvals
    endpoints), not a hand-built graph — and through the process-wide
    cached singleton, not an explicitly-injected fresh checkpointer.
    `get_postgres_checkpointer.cache_clear()` discards that singleton
    exactly like a process restart would: the pool object it points to
    becomes unreachable, so whatever runs after the clear can only succeed
    by reading the paused state back from Postgres, not from anything still
    held in this process's memory."""
    app.dependency_overrides[get_chat_fn] = lambda: ScriptedChatFn(
        responses=list(HAPPY_PATH_SCRIPT)
    )
    try:
        incident_id = client.post(
            "/api/v1/incidents", json={"scenario_key": "checkout-deploy-outage"}, headers=auth_headers
        ).json()["id"]
        run_resp = client.post(f"/api/v1/incidents/{incident_id}/run", headers=auth_headers)
        assert run_resp.json()["status"] == "awaiting_approval"

        stale_checkpointer = get_postgres_checkpointer()
        get_postgres_checkpointer.cache_clear()

        approve_resp = client.post(
            f"/api/v1/incidents/{incident_id}/approvals",
            json={"approved": True, "actor": "alice", "params": {"target_version": "v2.7"}},
            headers=auth_headers,
        )
        assert approve_resp.status_code == 200
        resolved = approve_resp.json()
        assert resolved["status"] == "diagnosed"
        assert resolved["diagnosis"]["recommended_action"] == "rollback_deployment"

        stale_checkpointer.conn.close()
    finally:
        app.dependency_overrides.pop(get_chat_fn, None)
