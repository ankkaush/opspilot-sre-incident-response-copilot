"""Durable, cross-process graph checkpointing (v0.5 Phase 1).

Before this phase, `opspilot.agent.graph` used a process-lifetime
`InMemorySaver` — real pause/resume within one running process, but a
restart lost every paused investigation (see v0.2 Phase 3's HITL design).
This module replaces it with LangGraph's Postgres checkpointer: every node
transition is durably persisted to the same Postgres database the rest of
the app already uses, so a paused or in-flight investigation survives a
process restart, not just a request boundary.

Ownership note: the checkpointer's own tables (`checkpoints`,
`checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) are
deliberately NOT managed by this project's Alembic migrations. They're
owned and versioned by langgraph-checkpoint-postgres itself via
`PostgresSaver.setup()` — idempotent, safe to call on every process start.
Hand-rolling Alembic migrations for a third-party library's internal schema
would couple us to its private implementation details for no real benefit;
the library already versions and migrates its own tables. `setup()` runs
lazily, once per process, the first time `get_postgres_checkpointer()` is
called — the same pattern `opspilot.agent.tracing.get_langfuse_client`
already uses for its own lazy singleton.

Security: the blueprint asks for checkpoint payloads to get the same
redaction scrutiny as Langfuse traces. GraphState — the only thing this
checkpointer ever persists; NodeDeps (which carries the live DB session and
chat_fn) is never serialized, only closed over — has no field that ever
holds an API key, password, or token (see opspilot.agent.state.GraphState).
That's a structural guarantee, not an active masking pass: there's nothing
shaped like a secret in the schema to redact. tests/test_checkpointer.py
verifies this directly against a real persisted checkpoint row, checking
the same secret-shaped key names opspilot.agent.tracing redacts.
"""

from functools import lru_cache

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from opspilot.config import get_settings

# GraphState stores these two as real Pydantic objects, not pre-flattened
# dicts (see opspilot.agent.state) — the default serializer warns that
# deserializing unregistered types will be blocked in a future
# langgraph-checkpoint release unless explicitly allowed. `True` (allow
# everything) would be the wrong fix here: an explicit allowlist of exactly
# our own two known types is the same trust boundary the warning itself is
# asking for, not a blanket opt-out of it.
_ALLOWED_MSGPACK_MODULES = [
    ("opspilot.agent.schemas", "SubmitDiagnosisArgs"),
    ("opspilot.agent.schemas", "ToolCallRecord"),
]


def _psycopg_conn_string(database_url: str) -> str:
    """SQLAlchemy's `postgresql+psycopg://` DSN scheme isn't a valid
    psycopg connection string on its own — psycopg (and this checkpointer,
    built directly on it, independent of SQLAlchemy) expects a plain
    `postgresql://` URL."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _build_checkpointer() -> PostgresSaver:
    settings = get_settings()
    pool = ConnectionPool(
        _psycopg_conn_string(settings.database_url),
        open=True,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    saver = PostgresSaver(pool, serde=serde)
    saver.setup()
    return saver


@lru_cache
def get_postgres_checkpointer() -> PostgresSaver:
    """The production singleton — one connection pool per process, reused
    across every investigation. Cached the same way
    opspilot.agent.tracing.get_langfuse_client is."""
    return _build_checkpointer()


def new_postgres_checkpointer() -> PostgresSaver:
    """An independent checkpointer with its own fresh connection pool —
    deliberately bypasses the cached singleton. Exists for
    tests/test_checkpointer.py's kill-and-resume harness: reusing the
    cached pool across a simulated "process restart" would prove nothing
    about durability (an in-memory checkpointer would pass the same test
    if you just didn't discard it). A genuinely new pool attaching to the
    same Postgres database is what makes that test meaningful."""
    return _build_checkpointer()
