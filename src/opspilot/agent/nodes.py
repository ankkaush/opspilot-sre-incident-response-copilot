"""The graph's nodes, plus the routing function that decides whether
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

from langgraph.types import interrupt
from pydantic import ValidationError

from opspilot.agent.client import TransientProviderError, call_with_retries, estimate_cost_usd
from opspilot.agent.policy import ProposedAction
from opspilot.agent.policy import evaluate as evaluate_policy_verdict
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

# Action types that can auto-execute (policy verdict EXECUTE) *and* have a
# real remediation tool to run. "no_action" is EXECUTE-verdict too but has
# nothing to call. rollback_deployment/toggle_feature_flag never reach here
# with EXECUTE in the current risk table, so they don't need an entry —
# when they do need real parameters (a target version, a flag name), that's
# v0.2 Phase 3's job, once the diagnosis pipeline can actually collect them
# through a human approval step rather than guessing.
_AUTO_EXECUTABLE = {
    "restart_service": lambda ctx: restart_service(ctx, RestartServiceArgs()),
    "scale_service": lambda ctx: scale_service(ctx, ScaleServiceArgs()),
}

# Action types that need a human's approval *and* concrete parameters before
# they can run — a real SRE approving a rollback specifies which version,
# they don't let the system guess. The API validates `params` against the
# same schema before ever resuming the graph (see routers/incidents.py);
# this node validates again, defense-in-depth, and simply doesn't execute
# if the params turn out to be invalid.
_APPROVABLE_ACTIONS = {
    "rollback_deployment": (RollbackDeploymentArgs, rollback_deployment),
    "toggle_feature_flag": (ToggleFeatureFlagArgs, toggle_feature_flag),
}

_TOOLS = [*anthropic_tool_definitions(), SUBMIT_DIAGNOSIS_TOOL]

# classify_risk's own preview table — informational only, superseded as the
# actual gate by opspilot.agent.policy (see evaluate_policy below). Kept
# distinct rather than merged: this one is a plain risk *tier* for display,
# the policy module returns an enforceable *verdict*.
_RISK_TABLE = {
    "no_action": "none",
    "escalate": "none",
    "restart_service": "low",
    "scale_service": "low",
    "toggle_feature_flag": "medium",
    "rollback_deployment": "medium",
    "delete_data": "critical",
}


def gather_context(state: GraphState, *, deps: NodeDeps) -> dict:
    """One model call, one round of tool execution. Whether this needs to
    run again is decided by `route_after_gather_context`, not by this
    function — the self-loop lives in the graph's edges, not in here."""
    ctx = ToolContext(session=deps.session, scenario=deps.scenario)
    settings = get_settings()
    step = state["steps_used"] + 1

    try:
        response = call_with_retries(
            deps.chat_fn, messages=state["messages"], tools=_TOOLS, system=system_prompt(deps.scenario)
        )
    except TransientProviderError as exc:
        # Bounded retry already happened inside call_with_retries and still
        # failed — this is the graceful give-up, not a crash: a defined
        # terminal state (route_after_gather_context sends this straight to
        # decide), not an unhandled exception bubbling out of the graph.
        log.error(
            "model call failed after exhausting retries — giving up gracefully",
            extra={"extra_fields": {"scenario_key": deps.scenario.key, "step": step, "error": str(exc)}},
        )
        return {"steps_used": step, "provider_error": str(exc)}

    cost = state["estimated_cost_usd"] + estimate_cost_usd(
        settings.anthropic_model, response.input_tokens, response.output_tokens
    )
    input_tokens = state["total_input_tokens"] + response.input_tokens
    output_tokens = state["total_output_tokens"] + response.output_tokens

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
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
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
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "diagnosis": diagnosis,
    }


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


def route_after_gather_context(state: GraphState, *, deps: NodeDeps) -> str:
    if state["provider_error"] is not None:
        # Give up now — looping back to gather_context would just repeat
        # the same exhausted-retries failure.
        return "ceiling"
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


def _execute_approved_action(deps: NodeDeps, action_type: str, params: dict | None) -> dict | None:
    entry = _APPROVABLE_ACTIONS.get(action_type)
    if entry is None:
        return None
    args_model, handler = entry
    try:
        validated = args_model.model_validate(params or {})
    except ValidationError as exc:
        log.warning(
            "approved action had invalid params — not executing",
            extra={"extra_fields": {"action_type": action_type, "error": str(exc)}},
        )
        return None
    ctx = RemediationContext(session=deps.session, scenario=deps.scenario)
    return handler(ctx, validated)


def _resolve_verdict(
    deps: NodeDeps, diagnosis: SubmitDiagnosisArgs, verdict, decision: dict | None
) -> dict:
    """The part of policy evaluation that has no interrupt() call in it —
    deliberately factored out so it's directly unit-testable (see
    tests/test_agent_nodes.py) without needing a live graph execution
    context, which raw `interrupt()` calls require and plain function calls
    don't provide.

    `decision` is None on every path except a resolved REQUIRE_APPROVAL
    (where `evaluate_policy` already has the human's — or the SLA-timeout
    path's — answer in hand before calling this)."""
    if verdict == "REQUIRE_APPROVAL":
        remediation_result = None
        if decision is not None and decision.get("approved"):
            remediation_result = _execute_approved_action(
                deps, diagnosis.recommended_action, decision.get("params")
            )
        return {
            "policy_verdict": verdict,
            "remediation_result": remediation_result,
            "approval_decision": decision,
        }

    remediation_result = None
    if verdict == "EXECUTE":
        executor = _AUTO_EXECUTABLE.get(diagnosis.recommended_action)
        if executor is not None:
            ctx = RemediationContext(session=deps.session, scenario=deps.scenario)
            remediation_result = executor(ctx)
        # "no_action" also verdicts EXECUTE but has no executor — nothing to
        # run, and remediation_result correctly stays None.

    return {"policy_verdict": verdict, "remediation_result": remediation_result, "approval_decision": None}


def evaluate_policy(state: GraphState, *, deps: NodeDeps) -> dict:
    """The real gate — `classify_risk` above is a preview; this is the
    module (opspilot.agent.policy) that actually decides, keyed only on
    `recommended_action`, never on the diagnosis text or its confidence.

    On REQUIRE_APPROVAL this calls `interrupt()`, which pauses the graph
    here (persisted via the process-level checkpointer in graph.py) until
    something resumes it with a decision. Everything above the interrupt
    call must be safe to re-run unchanged — LangGraph re-enters this
    function from the top on resume, and `interrupt()` only then returns
    the decision instead of pausing again. That's why the verdict
    computation above has no side effects: it's fine to redo it.
    """
    diagnosis = state["diagnosis"]
    if diagnosis is None:
        return {"policy_verdict": None, "remediation_result": None, "approval_decision": None}

    action = ProposedAction(
        action_type=diagnosis.recommended_action, target=deps.scenario.service.name, params={}
    )
    verdict = evaluate_policy_verdict(action)

    decision = None
    if verdict == "REQUIRE_APPROVAL":
        decision = interrupt(
            {
                "action_type": diagnosis.recommended_action,
                "service": deps.scenario.service.name,
                "diagnosis": diagnosis.diagnosis,
                "confidence": diagnosis.confidence,
            }
        )

    return _resolve_verdict(deps, diagnosis, verdict, decision)


def decide(state: GraphState, *, deps: NodeDeps) -> dict:
    if state["provider_error"] is not None:
        status = "incomplete_provider_error"
    elif state["diagnosis"] is not None:
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
