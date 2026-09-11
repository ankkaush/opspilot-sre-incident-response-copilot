"""The seed generator must be deterministic (v0.1 Phase 1's testing
requirement) and idempotent (safe to run on every app startup)."""

from opspilot.db import SessionLocal
from opspilot.models import Scenario
from opspilot.seed.generator import build_seed_payload, seed_all


def test_build_seed_payload_is_deterministic():
    first = build_seed_payload()
    second = build_seed_payload()
    assert first == second


def test_build_seed_payload_covers_expected_scenarios():
    payload = build_seed_payload()
    keys = {scenario["key"] for scenario in payload}
    assert {"checkout-deploy-outage", "payments-db-latency"} <= keys

    for scenario in payload:
        gt = scenario["ground_truth"]
        assert gt["expected_diagnosis"]
        assert gt["expected_action"]
        assert gt["expected_policy_verdict"] in {
            "EXECUTE",
            "REQUIRE_APPROVAL",
            "BLOCK",
            "ESCALATE",
        }


def test_seed_all_is_idempotent():
    session = SessionLocal()
    try:
        seed_all(session)
        count_after_first = session.query(Scenario).count()

        seed_all(session)
        count_after_second = session.query(Scenario).count()

        assert count_after_first == count_after_second
        assert count_after_first >= 2
    finally:
        session.close()
