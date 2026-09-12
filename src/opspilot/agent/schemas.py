"""Structured I/O for the agent loop.

`SubmitDiagnosisArgs` is the loop's final structured output — the agent
"submits" it as a tool call rather than free text, so the same Pydantic
validation path that guards every other tool also guards the answer itself.
"""

from typing import Literal

from pydantic import BaseModel, Field

from opspilot.agent.policy import PolicyVerdict

# The recommended-action vocabulary intentionally matches what the seed
# scenarios' ground_truth.expected_action already uses (opspilot.seed.scenarios)
# — that's what makes a later eval comparison (v0.3) a straight equality check.
RecommendedAction = Literal[
    "rollback_deployment",
    "restart_service",
    "scale_service",
    "toggle_feature_flag",
    "escalate",
    "no_action",
    # Always BLOCKed by the policy engine (opspilot.agent.policy) — an
    # irreversible action this system must never execute, no matter how the
    # evidence is framed. Included in the vocabulary (not just the policy
    # table) so a real diagnosis can actually name it and be blocked for
    # real, rather than BLOCK only ever being reachable via a synthetic
    # out-of-vocabulary action type in a unit test.
    "delete_data",
]


class SubmitDiagnosisArgs(BaseModel):
    diagnosis: str = Field(min_length=10, description="Plain-language root-cause explanation.")
    evidence: list[str] = Field(
        min_length=1,
        description=(
            "Short references to what was actually checked, e.g. "
            "'deployment:checkout-api:v2.8' or 'logs:connection pool exhausted'."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: RecommendedAction = Field(
        description=(
            "If evidence is incomplete or contradictory, use 'escalate' rather than guessing."
        )
    )


class ToolCallRecord(BaseModel):
    """One row of the evidence trail — what was called, with what, and what came back.

    This is the in-memory shape of what becomes the persisted audit log in
    v0.1 Phase 3 and the dashboard timeline entries described in the blueprint.
    """

    step: int
    tool_name: str
    arguments: dict
    result: dict | list | None = None
    error: str | None = None


class InvestigationResult(BaseModel):
    scenario_key: str
    status: Literal[
        "diagnosed",
        "awaiting_approval",
        "incomplete_step_ceiling",
        "incomplete_cost_ceiling",
        "incomplete_provider_error",
    ]
    diagnosis: SubmitDiagnosisArgs | None = None
    evidence_trail: list[ToolCallRecord] = Field(default_factory=list)
    steps_used: int
    estimated_cost_usd: float
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Set by the graph's hypothesize/classify_risk nodes (v0.2 Phase 1) —
    # None when the investigation never reached a diagnosis, since neither
    # node runs on the ceiling path.
    evidence_grounded: bool | None = None
    ungrounded_evidence: list[str] = Field(default_factory=list)
    risk_tier: str | None = None

    # Set by evaluate_policy (v0.2 Phase 2) — the actual gate, keyed only on
    # recommended_action, never on diagnosis text or confidence. None when
    # no diagnosis was reached. remediation_result is only ever populated
    # when policy_verdict is EXECUTE (or a REQUIRE_APPROVAL action was
    # subsequently approved) and there was something to run.
    policy_verdict: PolicyVerdict | None = None
    remediation_result: dict | None = None

    # v0.2 Phase 3 — human-in-the-loop. `pending_approval` is the interrupt
    # payload while status == "awaiting_approval" (what's being asked, of
    # whom, why); `approval_decision` is what a human (or the SLA-timeout
    # path) ultimately decided, once resolved.
    pending_approval: dict | None = None
    approval_decision: dict | None = None

    # v0.3 Phase 3 — set from opspilot.agent.tracing.trace_investigation's
    # yielded trace id. None whenever Langfuse isn't configured; never
    # raises either way, since tracing must never affect whether an
    # investigation itself succeeds.
    langfuse_trace_id: str | None = None
