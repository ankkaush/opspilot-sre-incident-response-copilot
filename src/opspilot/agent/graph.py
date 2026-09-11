"""Builds the LangGraph state machine and runs one investigation through it.

Graph shape:

    gather_context --continue--> gather_context   (self-loop: one model call
                                                     + tool round per visit)
    gather_context --diagnosed--> hypothesize --> classify_risk --> decide --> END
    gather_context --ceiling--> decide --> END

This replaces v0.1's single Python while-loop with something with the
property that loop didn't have: every one of these transitions is a
distinct, named, independently-invocable step, not a line inside one
function. Nothing about *what* the agent can do changed from v0.1 — same
five tools, same submit_diagnosis contract, same ceilings. What changed is
that the control flow is now data (a graph) instead of code (a loop body).
"""

from functools import partial

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.nodes import (
    classify_risk,
    decide,
    gather_context,
    hypothesize,
    route_after_gather_context,
)
from opspilot.agent.schemas import InvestigationResult
from opspilot.agent.state import GraphState, NodeDeps, initial_state
from opspilot.config import get_settings
from opspilot.models import Scenario


def build_graph(deps: NodeDeps):
    graph = StateGraph(GraphState)

    graph.add_node("gather_context", partial(gather_context, deps=deps))
    graph.add_node("hypothesize", partial(hypothesize, deps=deps))
    graph.add_node("classify_risk", partial(classify_risk, deps=deps))
    graph.add_node("decide", partial(decide, deps=deps))

    graph.add_edge(START, "gather_context")
    graph.add_conditional_edges(
        "gather_context",
        partial(route_after_gather_context, deps=deps),
        {"continue": "gather_context", "diagnosed": "hypothesize", "ceiling": "decide"},
    )
    graph.add_edge("hypothesize", "classify_risk")
    graph.add_edge("classify_risk", "decide")
    graph.add_edge("decide", END)

    return graph.compile()


def run_investigation(
    session: Session,
    scenario: Scenario,
    *,
    chat_fn: ChatFn,
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
) -> InvestigationResult:
    settings = get_settings()
    max_steps = max_steps if max_steps is not None else settings.agent_max_steps
    max_cost_usd = max_cost_usd if max_cost_usd is not None else settings.agent_max_cost_usd

    deps = NodeDeps(
        session=session,
        scenario=scenario,
        chat_fn=chat_fn,
        max_steps=max_steps,
        max_cost_usd=max_cost_usd,
    )
    compiled = build_graph(deps)

    # Generous margin over max_steps: the self-loop accounts for at most
    # max_steps visits, plus hypothesize/classify_risk/decide at the end.
    # Not load-bearing for correctness (the ceiling logic already stops the
    # loop) — just headroom so LangGraph's own recursion guard never fires
    # first and produces a confusing error instead of our own ceiling status.
    final_state = compiled.invoke(
        initial_state(), config={"recursion_limit": max_steps + 10}
    )

    return InvestigationResult(
        scenario_key=scenario.key,
        status=final_state["status"],
        diagnosis=final_state["diagnosis"],
        evidence_trail=final_state["evidence_trail"],
        steps_used=final_state["steps_used"],
        estimated_cost_usd=round(final_state["estimated_cost_usd"], 6),
        evidence_grounded=final_state["evidence_grounded"],
        ungrounded_evidence=final_state["ungrounded_evidence"],
        risk_tier=final_state["risk_tier"],
    )
