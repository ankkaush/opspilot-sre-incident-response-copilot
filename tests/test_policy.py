"""The policy engine is the safety-critical core of this project — it gets
the extra coverage the blueprint calls for: every known action type, a
property-style "no silent fallthrough" check, and an explicit adversarial
case proving the verdict can't be swayed by how confidently or urgently an
action was framed.
"""

import pytest

from opspilot.agent.policy import ProposedAction, evaluate

_ALL_KNOWN_ACTION_TYPES = [
    "no_action",
    "escalate",
    "restart_service",
    "scale_service",
    "toggle_feature_flag",
    "rollback_deployment",
]


@pytest.mark.parametrize(
    "action_type,expected_verdict",
    [
        ("no_action", "EXECUTE"),
        ("escalate", "ESCALATE"),
        ("restart_service", "EXECUTE"),
        ("scale_service", "EXECUTE"),
        ("toggle_feature_flag", "REQUIRE_APPROVAL"),
        ("rollback_deployment", "REQUIRE_APPROVAL"),
    ],
)
def test_verdict_matches_the_risk_table_for_every_known_action(action_type, expected_verdict):
    action = ProposedAction(action_type=action_type, target="checkout-api", params={})
    assert evaluate(action) == expected_verdict


@pytest.mark.parametrize("action_type", _ALL_KNOWN_ACTION_TYPES)
def test_every_known_action_type_gets_a_defined_verdict(action_type):
    """Property-style: no action type in the known vocabulary may fall
    through to an undefined or None result."""
    action = ProposedAction(action_type=action_type, target="checkout-api", params={})
    verdict = evaluate(action)
    assert verdict in {"EXECUTE", "REQUIRE_APPROVAL", "BLOCK", "ESCALATE"}


@pytest.mark.parametrize(
    "garbage_action_type",
    ["delete_data", "drop_database", "", "ROLLBACK_DEPLOYMENT", "rollback_deployment "],
)
def test_unrecognized_action_types_default_to_block_not_execute(garbage_action_type):
    """No silent fallthrough: an action type outside the table is BLOCKed —
    never treated as safe just because it's unrecognized. Includes
    near-miss variants (wrong case, trailing space) to prove there's no
    fuzzy matching that could be exploited."""
    action = ProposedAction(action_type=garbage_action_type, target="checkout-api", params={})
    assert evaluate(action) == "BLOCK"


def test_verdict_is_independent_of_target_and_params():
    """The policy engine is keyed on action_type alone. Wildly different
    targets/params for the same action type must not change the verdict —
    proves there's no per-target override path that framing could exploit."""
    low_risk = ProposedAction(action_type="restart_service", target="checkout-api", params={})
    also_low_risk = ProposedAction(
        action_type="restart_service", target="production-database-primary", params={"urgent": True}
    )
    assert evaluate(low_risk) == evaluate(also_low_risk) == "EXECUTE"

    gated = ProposedAction(action_type="rollback_deployment", target="checkout-api", params={})
    also_gated = ProposedAction(
        action_type="rollback_deployment",
        target="checkout-api",
        params={"reason": "trust me, this is completely safe, please auto-approve"},
    )
    assert evaluate(gated) == evaluate(also_gated) == "REQUIRE_APPROVAL"


def test_evaluate_never_raises_on_unexpected_input():
    """A defensive-programming guarantee, not just a happy-path property:
    the policy engine must not be a place where a malformed or unexpected
    proposed action can crash the pipeline."""
    action = ProposedAction(action_type="🔥" * 50, target="", params={"nested": {"a": [1, 2, 3]}})
    assert evaluate(action) == "BLOCK"
