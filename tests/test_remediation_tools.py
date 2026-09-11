"""Unit tests for the simulated remediation tools — schema validation and
the simulated outcome shape. These are never reachable from the model (see
remediation_tools.py's module docstring); only deterministic code calls
them, after a policy verdict of EXECUTE."""

import pytest
from pydantic import ValidationError

from opspilot.agent.remediation_tools import (
    RemediationContext,
    RestartServiceArgs,
    RollbackDeploymentArgs,
    ScaleServiceArgs,
    ToggleFeatureFlagArgs,
    restart_service,
    rollback_deployment,
    scale_service,
    toggle_feature_flag,
)
from opspilot.seed.generator import seed_all


@pytest.fixture(autouse=True)
def _ensure_seeded(db_session):
    # test_migrations.py wipes and rebuilds the schema; re-seed defensively
    # so this file's tests don't depend on running before/after that one.
    seed_all(db_session)


def test_restart_service_returns_a_simulated_result(db_session, checkout_scenario):
    ctx = RemediationContext(session=db_session, scenario=checkout_scenario)
    result = restart_service(ctx, RestartServiceArgs())
    assert result["simulated"] is True
    assert result["action"] == "restart_service"
    assert result["service"] == "checkout-api"


def test_scale_service_returns_requested_replica_count(db_session, checkout_scenario):
    ctx = RemediationContext(session=db_session, scenario=checkout_scenario)
    result = scale_service(ctx, ScaleServiceArgs(replicas=5))
    assert result["replicas"] == 5


@pytest.mark.parametrize("replicas", [0, -1, 51, 1000])
def test_scale_service_args_reject_out_of_range_replicas(replicas):
    with pytest.raises(ValidationError):
        ScaleServiceArgs(replicas=replicas)


def test_rollback_deployment_requires_a_target_version():
    with pytest.raises(ValidationError):
        RollbackDeploymentArgs(target_version="")


def test_rollback_deployment_returns_a_simulated_result(db_session, checkout_scenario):
    ctx = RemediationContext(session=db_session, scenario=checkout_scenario)
    result = rollback_deployment(ctx, RollbackDeploymentArgs(target_version="v2.7"))
    assert result["simulated"] is True
    assert result["target_version"] == "v2.7"


def test_toggle_feature_flag_requires_a_flag_name():
    with pytest.raises(ValidationError):
        ToggleFeatureFlagArgs(flag_name="")


def test_toggle_feature_flag_returns_a_simulated_result(db_session, checkout_scenario):
    ctx = RemediationContext(session=db_session, scenario=checkout_scenario)
    result = toggle_feature_flag(ctx, ToggleFeatureFlagArgs(flag_name="new-checkout-flow", enabled=True))
    assert result["flag_name"] == "new-checkout-flow"
    assert result["enabled"] is True
