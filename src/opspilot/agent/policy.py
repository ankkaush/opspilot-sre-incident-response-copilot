"""The deterministic policy engine — the one place in this system where an
LLM's proposed action is checked against a fixed table instead of trusted.

This is the project's core rule made concrete:

    LLM recommendation -> policy engine -> EXECUTE / REQUIRE_APPROVAL / BLOCK

`evaluate()` is a function of `action_type` alone. It never sees the
diagnosis text, the model's stated confidence, or any other model-authored
free text — by construction, not by discipline. A risk table keyed on
anything an adversarial or overconfident prompt could shape (like a
self-reported "confidence": 1.0) would defeat the entire point of a policy
gate: the LLM must not be able to talk its way into a higher trust tier.
"""

import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("opspilot.policy")

PolicyVerdict = Literal["EXECUTE", "REQUIRE_APPROVAL", "BLOCK", "ESCALATE"]


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    target: str
    params: dict


# The single source of truth for what this system may do without a human,
# what needs one, and what it can never do — regardless of how the action
# was framed. "escalate" isn't a remediation action at all (there's nothing
# to gate — it's "hand this to a human"), so it maps straight to its own
# verdict rather than a risk tier. "no_action" is trivially safe.
_RISK_TABLE: dict[str, PolicyVerdict] = {
    "no_action": "EXECUTE",
    "escalate": "ESCALATE",
    "restart_service": "EXECUTE",
    "scale_service": "EXECUTE",
    "toggle_feature_flag": "REQUIRE_APPROVAL",
    "rollback_deployment": "REQUIRE_APPROVAL",
    # Explicitly BLOCKed, not just defaulted — this action is *in* the
    # vocabulary specifically so it can be recommended (and then actually
    # blocked) rather than being unreachable. No amount of human approval
    # ever turns this into EXECUTE; there's no code path that lets it.
    "delete_data": "BLOCK",
}


def evaluate(action: ProposedAction) -> PolicyVerdict:
    """Never raises, never falls through silently. An action type outside
    the table is BLOCKed by default — not treated as safe because it's
    unrecognized. This matters more than it looks: it means adding a new
    remediation tool without adding it to `_RISK_TABLE` fails closed, not
    open."""
    verdict = _RISK_TABLE.get(action.action_type)
    if verdict is None:
        log.warning(
            "policy: unrecognized action type, defaulting to BLOCK",
            extra={"extra_fields": {"action_type": action.action_type, "target": action.target}},
        )
        return "BLOCK"
    return verdict
