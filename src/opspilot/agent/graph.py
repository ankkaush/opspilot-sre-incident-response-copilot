"""Builds the LangGraph state machine and runs (or resumes) one
investigation through it.

Graph shape:

    gather_context --continue--> gather_context   (self-loop: one model call
                                                     + tool round per visit)
    gather_context --diagnosed--> hypothesize --> classify_risk
                                                     --> evaluate_policy --> decide --> END
    gather_context --ceiling--> decide --> END

`evaluate_policy` calls `interrupt()` when the policy verdict is
REQUIRE_APPROVAL, which pauses the graph mid-node. Resuming it later — from
`resume_investigation`, possibly in an entirely different HTTP request — is
what `_CHECKPOINTER` exists for.

`_CHECKPOINTER` is a process-lifetime `InMemorySaver`, deliberately not a
database-backed one: this gives real pause/resume within one running
process, but a restart loses every paused investigation. That's the correct
scope for this phase — durable, restart-surviving checkpointing is v0.5's
job, not v0.2's. A resumed investigation doesn't need the same compiled
graph *object* as the one that paused it (see the note in run_investigation)
— only the same checkpointer instance and thread_id — which is exactly what
makes resuming from a fresh HTTP request (a fresh DB session, a fresh
NodeDeps) work at all.
"""

from functools import partial

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.nodes import (
    classify_risk,
    decide,
    evaluate_policy,
    gather_context,
    hypothesize,
    route_after_gather_context,
)
from opspilot.agent.schemas import InvestigationResult
from opspilot.agent.state import GraphState, NodeDeps, initial_state
from opspilot.agent.support import PROMPT_VERSION
from opspilot.agent.tracing import to_jsonable, trace_investigation, trace_node
from opspilot.config import get_settings
from opspilot.memory import format_memory_for_prompt, retrieve_relevant_memory
from opspilot.models import Scenario

_CHECKPOINTER = InMemorySaver()


def _traced(name: str, node_fn):
    """Wraps a node function that's simple enough to trace generically —
    one span per call, input/output captured whole. gather_context is
    deliberately NOT wrapped this way; its internal model-call and
    tool-call loop gets its own finer-grained spans directly inside
    nodes.py instead of one span covering the whole thing."""

    def wrapped(state):
        with trace_node(name, input=to_jsonable({"diagnosis": state.get("diagnosis")})) as span:
            result = node_fn(state)
            span.update(output=to_jsonable(result))
            return result

    return wrapped


def build_graph(deps: NodeDeps):
    graph = StateGraph(GraphState)

    graph.add_node("gather_context", partial(gather_context, deps=deps))
    graph.add_node("hypothesize", _traced("hypothesize", partial(hypothesize, deps=deps)))
    graph.add_node("classify_risk", _traced("classify_risk", partial(classify_risk, deps=deps)))
    graph.add_node(
        "evaluate_policy", _traced("evaluate_policy", partial(evaluate_policy, deps=deps))
    )
    graph.add_node("decide", _traced("decide", partial(decide, deps=deps)))

    graph.add_edge(START, "gather_context")
    graph.add_conditional_edges(
        "gather_context",
        partial(route_after_gather_context, deps=deps),
        {"continue": "gather_context", "diagnosed": "hypothesize", "ceiling": "decide"},
    )
    graph.add_edge("hypothesize", "classify_risk")
    graph.add_edge("classify_risk", "evaluate_policy")
    graph.add_edge("evaluate_policy", "decide")
    graph.add_edge("decide", END)

    return graph.compile(checkpointer=_CHECKPOINTER)


def _build_deps(
    session: Session,
    scenario: Scenario,
    chat_fn: ChatFn,
    max_steps: int | None,
    max_cost_usd: float | None,
    *,
    memory_enabled: bool = True,
) -> NodeDeps:
    settings = get_settings()
    # v0.4 Phase 2 — retrieved once per investigation, not per gather_context
    # iteration. memory_enabled=False is the eval harness's on/off lever for
    # measuring retrieval's actual impact (opspilot.eval.runner); real
    # incidents always leave it at the default.
    memory_context = ""
    if memory_enabled:
        memory_rows = retrieve_relevant_memory(session, scenario.service_id)
        memory_context = format_memory_for_prompt(memory_rows)
    return NodeDeps(
        session=session,
        scenario=scenario,
        chat_fn=chat_fn,
        max_steps=max_steps if max_steps is not None else settings.agent_max_steps,
        max_cost_usd=max_cost_usd if max_cost_usd is not None else settings.agent_max_cost_usd,
        memory_context=memory_context,
    )


def _result_from_state(
    scenario: Scenario, final_state: dict, *, langfuse_trace_id: str | None
) -> InvestigationResult:
    interrupts = final_state.get("__interrupt__")
    if interrupts:
        # Paused mid-graph. `interrupts[0].value` is exactly the payload
        # dict evaluate_policy passed to interrupt() — what's being asked,
        # of whom, and why. Every other field reflects the state as of the
        # moment it paused (the diagnosis already exists; policy_verdict and
        # remediation_result don't yet — evaluate_policy hasn't returned).
        return InvestigationResult(
            scenario_key=scenario.key,
            status="awaiting_approval",
            diagnosis=final_state["diagnosis"],
            evidence_trail=final_state["evidence_trail"],
            steps_used=final_state["steps_used"],
            estimated_cost_usd=round(final_state["estimated_cost_usd"], 6),
            total_input_tokens=final_state["total_input_tokens"],
            total_output_tokens=final_state["total_output_tokens"],
            evidence_grounded=final_state["evidence_grounded"],
            ungrounded_evidence=final_state["ungrounded_evidence"],
            risk_tier=final_state["risk_tier"],
            pending_approval=interrupts[0].value,
            langfuse_trace_id=langfuse_trace_id,
        )

    return InvestigationResult(
        scenario_key=scenario.key,
        status=final_state["status"],
        diagnosis=final_state["diagnosis"],
        evidence_trail=final_state["evidence_trail"],
        steps_used=final_state["steps_used"],
        estimated_cost_usd=round(final_state["estimated_cost_usd"], 6),
        total_input_tokens=final_state["total_input_tokens"],
        total_output_tokens=final_state["total_output_tokens"],
        evidence_grounded=final_state["evidence_grounded"],
        ungrounded_evidence=final_state["ungrounded_evidence"],
        risk_tier=final_state["risk_tier"],
        langfuse_trace_id=langfuse_trace_id,
        policy_verdict=final_state["policy_verdict"],
        remediation_result=final_state["remediation_result"],
        approval_decision=final_state["approval_decision"],
    )


def run_investigation(
    session: Session,
    scenario: Scenario,
    *,
    chat_fn: ChatFn,
    thread_id: str,
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
    memory_enabled: bool = True,
) -> InvestigationResult:
    deps = _build_deps(
        session, scenario, chat_fn, max_steps, max_cost_usd, memory_enabled=memory_enabled
    )
    compiled = build_graph(deps)

    # Generous margin over max_steps: the self-loop accounts for at most
    # max_steps visits, plus hypothesize/classify_risk/evaluate_policy/decide
    # at the end. Not load-bearing for correctness (the ceiling logic
    # already stops the loop) — just headroom so LangGraph's own recursion
    # guard never fires first and produces a confusing error instead of our
    # own ceiling status.
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": deps.max_steps + 10}
    with trace_investigation(
        scenario_key=scenario.key, thread_id=thread_id, prompt_version=PROMPT_VERSION
    ) as trace_id:
        final_state = compiled.invoke(initial_state(), config=config)
    return _result_from_state(scenario, final_state, langfuse_trace_id=trace_id)


def resume_investigation(
    session: Session,
    scenario: Scenario,
    *,
    chat_fn: ChatFn,
    thread_id: str,
    decision: dict,
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
    memory_enabled: bool = True,
) -> InvestigationResult:
    """Resumes a paused investigation with a human's (or the SLA-timeout
    path's) decision. `decision` becomes evaluate_policy's `interrupt()`
    return value on re-entry — see that function for its shape.

    Deliberately builds a brand-new NodeDeps and a brand-new compiled graph
    object rather than trying to reuse whatever ran the original
    `run_investigation` call: that call's DB session may be long closed by
    the time a human actually responds. Only `_CHECKPOINTER` and
    `thread_id` need to be the same — LangGraph resumes from the persisted
    state, not from the old Python objects.

    `memory_context` on this fresh NodeDeps would never actually be read —
    a resume re-enters at evaluate_policy, not gather_context — so
    retrieval is always skipped here regardless of `memory_enabled`; the
    parameter exists only to keep this function's signature symmetric with
    run_investigation's.
    """
    deps = _build_deps(session, scenario, chat_fn, max_steps, max_cost_usd, memory_enabled=False)
    compiled = build_graph(deps)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": deps.max_steps + 10}
    # A resume gets its own trace rather than continuing the original one —
    # it may run in an entirely different process, arbitrarily long after
    # the pause. Both traces carry the same thread_id in their metadata, so
    # a human can still find the pair by filtering on it in the Langfuse UI.
    with trace_investigation(
        scenario_key=scenario.key, thread_id=thread_id, prompt_version=PROMPT_VERSION
    ) as trace_id:
        final_state = compiled.invoke(Command(resume=decision), config=config)
    return _result_from_state(scenario, final_state, langfuse_trace_id=trace_id)
