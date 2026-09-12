"""The seed generator must be deterministic (v0.1 Phase 1's testing
requirement) and idempotent (safe to run on every app startup). This file
also holds v0.3 Phase 1's "done when" checks for the golden dataset itself:
every scenario has ground truth reviewable by a human without running the
agent, and the dataset covers all four policy verdicts."""

from opspilot.db import SessionLocal
from opspilot.models import Scenario
from opspilot.seed.generator import build_seed_payload, seed_all
from opspilot.seed.scenarios import ALL_SCENARIOS


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


def test_dataset_size_is_within_the_blueprint_target():
    """15-25 scenarios — enough for real coverage without the diminishing-
    returns problem of building hundreds (see the blueprint's own risk
    note on this)."""
    assert 15 <= len(ALL_SCENARIOS) <= 25


def test_dataset_scenario_keys_are_unique():
    keys = [s.key for s in ALL_SCENARIOS]
    assert len(keys) == len(set(keys))


def test_dataset_covers_every_policy_verdict_at_least_once():
    verdicts = {s.ground_truth.expected_policy_verdict for s in ALL_SCENARIOS}
    assert verdicts == {"EXECUTE", "REQUIRE_APPROVAL", "BLOCK", "ESCALATE"}


def test_every_scenario_has_ground_truth_reviewable_without_running_the_agent():
    """A human should be able to read expected_diagnosis and
    expected_evidence and understand what the scenario is testing, without
    ever invoking the agent — that's the whole point of writing ground
    truth up front instead of after the fact."""
    for spec in ALL_SCENARIOS:
        gt = spec.ground_truth
        assert len(gt.expected_diagnosis) >= 30, f"{spec.key}: diagnosis too thin to review"
        assert len(gt.expected_evidence) >= 1, f"{spec.key}: no expected evidence listed"
        assert gt.expected_action, f"{spec.key}: no expected action"
        assert spec.injected_cause, f"{spec.key}: no injected cause documented"


def test_seed_all_is_idempotent():
    session = SessionLocal()
    try:
        seed_all(session)
        count_after_first = session.query(Scenario).count()

        seed_all(session)
        count_after_second = session.query(Scenario).count()

        assert count_after_first == count_after_second
        assert count_after_first == len(ALL_SCENARIOS)
    finally:
        session.close()
