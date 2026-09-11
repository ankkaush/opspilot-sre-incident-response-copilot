"""The graph's four nodes, plus the routing function that decides whether
`gather_context` loops back on itself or the investigation moves on.

Each node is a plain function of `(state, deps)` returning a partial state
update — no hidden control flow, no shared mutable loop variables. That's
the concrete difference from v0.1's while-loop this phase exists to
demonstrate: every one of these is callable and assertable on its own, in
isolation, which tests/test_agent_nodes.py does directly.
"""

import json
import logging
import re

from pydantic import ValidationError

from opspilot.agent.client import call_with_retries, estimate_cost_usd
from opspilot.agent.schemas import SubmitDiagnosisArgs, ToolCallRecord
from opspilot.agent.state import GraphState, NodeDeps
from opspilot.agent.support import (
    SUBMIT_DIAGNOSIS_TOOL,
    content_blocks_to_dicts,
    system_prompt,
    truncate_tool_result,
)
from opspilot.agent.tools import TOOL_REGISTRY, ToolContext, anthropic_tool_definitions
from opspilot.config import get_settings

log = logging.getLogger("opspilot.agent")

_TOOLS = [*anthropic_tool_definitions(), SUBMIT_DIAGNOSIS_TOOL]

# Preview of the risk classification the v0.2 Phase 2 policy engine will
# formalize and actually enforce. This node's output is informational only
# in Phase 1 — nothing here gates anything yet, because nothing here can be
# executed yet (remediation tools don't exist until Phase 2).
_RISK_TABLE = {
    "no_action": "none",
    "escalate": "none",
    "restart_service": "low",
    "scale_service": "low",
    "toggle_feature_flag": "medium",
    "rollback_deployment": "medium",
}


def gather_context(state: GraphState, *, deps: NodeDeps) -> dict:
    """One model call, one round of tool execution. Whether this needs to
    run again is decided by `route_after_gather_context`, not by this
    function — the self-loop lives in the graph's edges, not in here."""
    ctx = ToolContext(session=deps.session, scenario=deps.scenario)
    settings = get_settings()

    response = call_with_retries(
        deps.chat_fn, messages=state["messages"], tools=_TOOLS, system=system_prompt(deps.scenario)
    )
    step = state["steps_used"] + 1
    cost = state["estimated_cost_usd"] + estimate_cost_usd(
        settings.anthropic_model, response.input_tokens, response.output_tokens
    )

    new_messages: list[dict] = [
        {"role": "assistant", "content": content_blocks_to_dicts(response.content)}
    ]
    tool_use_blocks = [b for b in response.content if hasattr(b, "name")]

    if not tool_use_blocks:
        new_messages.append(
            {
                "role": "user",
                "content": "Continue investigating by calling a tool, or call submit_diagnosis "
                "when ready.",
            }
        )
        return {
            "messages": new_messages,
            "steps_used": step,
            "estimated_cost_usd": cost,
        }

    tool_results: list[dict] = []
    new_evidence: list[ToolCallRecord] = []
    diagnosis: SubmitDiagnosisArgs | None = None

    for block in tool_use_blocks:
        if block.name == "submit_diagnosis":
            try:
                diagnosis = SubmitDiagnosisArgs.model_validate(block.input)
            except ValidationError as exc:
                error_text = f"Invalid diagnosis: {exc}"
                tool_results.append(_tool_result(block.id, error_text, is_error=True))
                new_evidence.append(
                    ToolCallRecord(step=step, tool_name=block.name, arguments=block.input, error=error_text)
                )
                continue
            new_evidence.append(
                ToolCallRecord(
                    step=step, tool_name=block.name, arguments=block.input, result=diagnosis.model_dump()
                )
            )
            tool_results.append(_tool_result(block.id, "Diagnosis received."))
            continue

        spec = TOOL_REGISTRY.get(block.name)
        if spec is None:
            error_text = f"Unknown tool '{block.name}'."
            tool_results.append(_tool_result(block.id, error_text, is_error=True))
            new_evidence.append(
                ToolCallRecord(step=step, tool_name=block.name, arguments=block.input, error=error_text)
            )
            continue

        try:
            validated_args = spec.args_model.model_validate(block.input)
        except ValidationError as exc:
            error_text = f"Invalid arguments: {exc}"
            tool_results.append(_tool_result(block.id, error_text, is_error=True))
            new_evidence.append(
                ToolCallRecord(step=step, tool_name=block.name, arguments=block.input, error=error_text)
            )
            continue

        result = spec.handler(ctx, validated_args)
        safe_result = truncate_tool_result(result)
        tool_results.append(_tool_result(block.id, json.dumps(safe_result, default=str)))
        new_evidence.append(
            ToolCallRecord(
                step=step,
                tool_name=block.name,
                arguments=validated_args.model_dump(),
                result=safe_result if isinstance(safe_result, (dict, list)) else None,
            )
        )

    new_messages.append({"role": "user", "content": tool_results})

    return {
        "messages": new_messages,
        "evidence_trail": new_evidence,
        "steps_used": step,
        "estimated_cost_usd": cost,
        "diagnosis": diagnosis,
    }


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


def route_after_gather_context(state: GraphState, *, deps: NodeDeps) -> str:
    if state["diagnosis"] is not None:
        return "diagnosed"
    if state["estimated_cost_usd"] > deps.max_cost_usd or state["steps_used"] >= deps.max_steps:
        return "ceiling"
    return "continue"


def _looks_grounded(evidence_citation: str, gathered_blob: str) -> bool:
    """Crude but deterministic: a citation is 'grounded' if at least one
    meaningful token from it actually shows up somewhere in what was
    gathered during the investigation. Not a semantic check — a hallucinated
    citation that happens to reuse real words would slip past this. Good
    enough as a first, cheap guard; the v0.3 LLM-as-judge groundedness
    rubric is the real check."""
    tokens = [t for t in re.split(r"[:\s]+", evidence_citation.lower()) if len(t) > 3]
    if not tokens:
        return True
    return any(token in gathered_blob for token in tokens)


def hypothesize(state: GraphState, *, deps: NodeDeps) -> dict:
    """Deterministic evidence-grounding check on the diagnosis the model
    already formed in `gather_context` — the model does the judgment
    (forming the hypothesis), code does the verification (did it actually
    cite things it gathered)."""
    diagnosis = state["diagnosis"]
    if diagnosis is None:
        return {"evidence_grounded": None, "ungrounded_evidence": []}

    gathered_blob = json.dumps(
        [r.model_dump() for r in state["evidence_trail"]], default=str
    ).lower()
    ungrounded = [e for e in diagnosis.evidence if not _looks_grounded(e, gathered_blob)]
    return {"evidence_grounded": len(ungrounded) == 0, "ungrounded_evidence": ungrounded}


def classify_risk(state: GraphState, *, deps: NodeDeps) -> dict:
    diagnosis = state["diagnosis"]
    if diagnosis is None:
        return {"risk_tier": None}
    return {"risk_tier": _RISK_TABLE.get(diagnosis.recommended_action, "unknown")}


def decide(state: GraphState, *, deps: NodeDeps) -> dict:
    if state["diagnosis"] is not None:
        status = "diagnosed"
    elif state["estimated_cost_usd"] > deps.max_cost_usd:
        status = "incomplete_cost_ceiling"
    else:
        status = "incomplete_step_ceiling"

    if status != "diagnosed":
        log.warning(
            "investigation ended without diagnosis",
            extra={
                "extra_fields": {
                    "scenario_key": deps.scenario.key,
                    "status": status,
                    "steps_used": state["steps_used"],
                    "estimated_cost_usd": round(state["estimated_cost_usd"], 6),
                }
            },
        )

    return {"status": status}
