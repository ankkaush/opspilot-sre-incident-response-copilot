"""Structured I/O for the agent loop.

`SubmitDiagnosisArgs` is the loop's final structured output — the agent
"submits" it as a tool call rather than free text, so the same Pydantic
validation path that guards every other tool also guards the answer itself.
"""

from typing import Literal

from pydantic import BaseModel, Field

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
        "incomplete_step_ceiling",
        "incomplete_cost_ceiling",
    ]
    diagnosis: SubmitDiagnosisArgs | None = None
    evidence_trail: list[ToolCallRecord] = Field(default_factory=list)
    steps_used: int
    estimated_cost_usd: float

    # Set by the graph's hypothesize/classify_risk nodes (v0.2 Phase 1) —
    # None when the investigation never reached a diagnosis, since neither
    # node runs on the ceiling path.
    evidence_grounded: bool | None = None
    ungrounded_evidence: list[str] = Field(default_factory=list)
    risk_tier: str | None = None
