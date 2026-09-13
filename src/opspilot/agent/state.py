"""The graph's state schema and the dependencies its nodes close over.

`GraphState` is a TypedDict rather than a raw Pydantic BaseModel — LangGraph's
merge semantics (per-key reducers via `Annotated[..., operator.add]`, "last
write wins" for everything else) are built around TypedDict/dataclass state
schemas, and that's what's actually well-supported and documented. The
"Pydantic-typed state" the blueprint asks for is satisfied one level down:
`evidence_trail` holds `ToolCallRecord` Pydantic models and `diagnosis` holds
a `SubmitDiagnosisArgs` Pydantic model — the complex, validated pieces of
state are real Pydantic objects, not untyped dicts. The outer TypedDict is
just the container LangGraph knows how to merge.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, TypedDict

from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.policy import PolicyVerdict
from opspilot.agent.schemas import SubmitDiagnosisArgs, ToolCallRecord
from opspilot.models import Scenario


class GraphState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    evidence_trail: Annotated[list[ToolCallRecord], operator.add]
    steps_used: int
    estimated_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    diagnosis: SubmitDiagnosisArgs | None
    evidence_grounded: bool | None
    ungrounded_evidence: list[str]
    risk_tier: str | None
    policy_verdict: PolicyVerdict | None
    remediation_result: dict | None
    # Set by evaluate_policy only on the REQUIRE_APPROVAL path, once a human
    # decision has actually come back through interrupt()/resume — None
    # while genuinely pending, and (importantly) still None on every path
    # that never needed a human at all.
    approval_decision: dict | None
    # Set by gather_context if the model call fails after exhausting
    # retries — the graceful-give-up path, distinct from a crash.
    provider_error: str | None
    status: str | None


def initial_state() -> GraphState:
    return GraphState(
        messages=[{"role": "user", "content": "Investigate this incident and submit your diagnosis."}],
        evidence_trail=[],
        steps_used=0,
        estimated_cost_usd=0.0,
        total_input_tokens=0,
        total_output_tokens=0,
        diagnosis=None,
        evidence_grounded=None,
        ungrounded_evidence=[],
        risk_tier=None,
        policy_verdict=None,
        remediation_result=None,
        approval_decision=None,
        provider_error=None,
        status=None,
    )


@dataclass(frozen=True)
class NodeDeps:
    """Everything a node needs beyond the graph state itself — the same role
    `ToolContext` plays for individual tools, one level up. Passed to every
    node via `functools.partial`, which is also what makes each node
    independently callable (and independently testable) outside a graph."""

    session: Session
    scenario: Scenario
    chat_fn: ChatFn
    max_steps: int
    max_cost_usd: float
    # v0.4 Phase 2 — pre-formatted "prior related incidents" text (see
    # opspilot.memory.format_memory_for_prompt), computed once per
    # investigation rather than re-queried on every gather_context
    # self-loop iteration. "" when there's nothing to surface or retrieval
    # was disabled (eval's memory-on/off comparison) — system_prompt()
    # treats that as "no memory section" either way.
    memory_context: str = ""
