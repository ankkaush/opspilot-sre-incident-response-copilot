import os

import pytest
from fastapi.testclient import TestClient

from opspilot.agent.checkpointer import get_postgres_checkpointer
from opspilot.db import SessionLocal
from opspilot.main import app
from opspilot.models import Scenario
from opspilot.seed.generator import seed_all


@pytest.fixture(scope="session", autouse=True)
def _seed_once():
    """Seed the synthetic environment once per test session.

    test_migrations.py wipes and rebuilds the schema mid-session; any test
    that needs seeded data after that point re-seeds itself (seed_all is
    idempotent, so this is always safe to call again).
    """
    session = SessionLocal()
    try:
        seed_all(session)
    finally:
        session.close()


@pytest.fixture(scope="session", autouse=True)
def _clear_checkpoints_once():
    """Checkpointing is durable across process restarts as of v0.5 Phase 1
    (opspilot.agent.checkpointer) — that's the whole point. Which means a
    fixed thread_id a test used in some *previous* pytest invocation
    against this same Postgres container would otherwise still be sitting
    there, and a graph invoked with that thread_id would resume stale
    state instead of starting fresh. Truncate once per session so every
    test run starts from a clean checkpoint store, the same way the rest
    of this suite's data is expected to be reproducible from run to run.
    """
    checkpointer = get_postgres_checkpointer()  # ensures setup() has run first
    with checkpointer.conn.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": os.environ["API_KEY"]}


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def checkout_scenario(db_session) -> Scenario:
    return db_session.query(Scenario).filter_by(key="checkout-deploy-outage").one()


@pytest.fixture
def payments_scenario(db_session) -> Scenario:
    return db_session.query(Scenario).filter_by(key="payments-db-latency").one()
