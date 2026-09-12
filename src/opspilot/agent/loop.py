"""Public entrypoint for running an investigation.

Through v0.1 this module *was* the agent: an explicit while-loop calling the
model, parsing tool calls, validating, executing, repeating. As of v0.2
Phase 1 that control flow has moved to opspilot.agent.graph — a LangGraph
state machine (opspilot.agent.nodes has the individual steps) — because the
while-loop had no inspectable intermediate state and no clean point to pause
or resume from. Nothing about what the agent can *do* changed: same five
tools, same submit_diagnosis contract, same deterministic ceilings.

`investigate()` keeps the return type and nearly the signature it always
had (routers/incidents.py calls run_investigation/resume_investigation
directly now that both exist, since it needs a stable thread_id across
separate HTTP requests) — the graph stays an internal implementation
detail of "how an investigation runs," not a new public contract.
"""

from uuid import uuid4

from sqlalchemy.orm import Session

from opspilot.agent.client import AnthropicChatClient, ChatFn, ModelResponse
from opspilot.agent.graph import run_investigation
from opspilot.agent.schemas import InvestigationResult
from opspilot.config import get_settings
from opspilot.models import Scenario


def _default_chat_fn() -> ChatFn:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in .env, or pass an explicit chat_fn "
            "(tests do this to avoid calling the real API)."
        )
    return AnthropicChatClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)


def get_chat_fn() -> ChatFn:
    """FastAPI-dependency-shaped wrapper around the default chat function.

    Exists so routers/incidents.py can `Depends(get_chat_fn)` and tests can
    override it via `app.dependency_overrides[get_chat_fn] = ...` — the real
    Anthropic client is never constructed in a test process.

    Deliberately lazy: FastAPI resolves every Depends() *before* the route
    body runs, so a naive `return _default_chat_fn()` here would try to
    build a real Anthropic client (and raise if ANTHROPIC_API_KEY is unset)
    even for a request that's about to 404 on a nonexistent incident and
    never actually call the model. The real client is only constructed the
    first time this wrapper is actually invoked.
    """

    def _lazy_chat_fn(*, messages: list[dict], tools: list[dict], system: str) -> ModelResponse:
        return _default_chat_fn()(messages=messages, tools=tools, system=system)

    return _lazy_chat_fn


def investigate(
    session: Session,
    scenario: Scenario,
    *,
    chat_fn: ChatFn | None = None,
    thread_id: str | None = None,
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
) -> InvestigationResult:
    """Convenience wrapper for a single run. Since v0.2 Phase 3, an
    investigation can legitimately come back with status "awaiting_approval"
    instead of finishing — resuming it is `opspilot.agent.graph.
    resume_investigation`, called with the same `thread_id` you pass here
    (or read back from nowhere, if you let this generate one — pass an
    explicit `thread_id` whenever you might need to resume)."""
    return run_investigation(
        session,
        scenario,
        chat_fn=chat_fn or _default_chat_fn(),
        thread_id=thread_id or f"investigate-{uuid4()}",
        max_steps=max_steps,
        max_cost_usd=max_cost_usd,
    )
