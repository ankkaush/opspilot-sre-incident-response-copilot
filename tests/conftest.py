import os

import pytest
from fastapi.testclient import TestClient

from opspilot.db import SessionLocal
from opspilot.main import app
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


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"X-API-Key": os.environ["API_KEY"]}
