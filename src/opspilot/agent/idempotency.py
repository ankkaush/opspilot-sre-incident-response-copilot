"""Idempotent remediation execution (v0.5 Phase 2).

Phase 1 made checkpointing durable across a process restart. This module
closes the gap that durability alone doesn't: LangGraph's documented
contract is that resuming past an `interrupt()` call re-enters the node
function *from the top* (see `opspilot.agent.nodes.evaluate_policy`'s own
docstring) — everything before the interrupt must be safe to redo, and
LangGraph replays past already-answered interrupts automatically. A crash
that happens after a human's approval decision is known, but before
`evaluate_policy`'s own checkpoint is durably written, means a later resume
reaches the remediation call a second time for the identical decision.
Nothing here is a LangGraph bug — it's the contract every node must hold
up on its own, and remediation execution is the one place in this graph
where "just redo it" isn't acceptable.

`execute_idempotently` is the fix: a deterministic key over
(thread_id, action_type, target, params), claimed in its own,
immediately-committed session — deliberately not the long-lived
per-investigation session `NodeDeps.session` carries, since this guarantee
must hold even if that session's own eventual commit never runs (the
surrounding request crashed first). A second attempt at the identical key
returns the first attempt's recorded result without calling `executor`
again, and reports that fact back (`deduplicated=True`) so a crash-and-
resume is visible in the incident's audit trail rather than silently
invisible — see opspilot.routers.incidents, which folds this flag into the
same `remediation_result` payload the timeline already exposes.
"""

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable

from opspilot.db import SessionLocal
from opspilot.models import RemediationExecution

log = logging.getLogger("opspilot.idempotency")


def _idempotency_key(*, thread_id: str, action_type: str, target: str, params: dict) -> str:
    payload = json.dumps(
        {"thread_id": thread_id, "action_type": action_type, "target": target, "params": params},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def execute_idempotently(
    *, thread_id: str, action_type: str, target: str, params: dict, executor: Callable[[], dict]
) -> tuple[dict, bool]:
    """Runs `executor()` at most once per (thread_id, action_type, target,
    params) tuple, ever. Returns `(result, deduplicated)` — `deduplicated`
    is True when this exact action had already executed, the concrete,
    queryable signal that a crash-and-resume (or an operational retry)
    actually happened, rather than something silently re-running unnoticed.
    """
    key = _idempotency_key(thread_id=thread_id, action_type=action_type, target=target, params=params)
    session = SessionLocal()
    try:
        existing = session.query(RemediationExecution).filter_by(idempotency_key=key).one_or_none()
        if existing is not None:
            log.warning(
                "remediation action already executed — skipping a duplicate call "
                "(crash-and-resume or a retried request)",
                extra={
                    "extra_fields": {
                        "thread_id": thread_id,
                        "action_type": action_type,
                        "target": target,
                        "idempotency_key": key,
                    }
                },
            )
            return existing.result, True

        result = executor()
        session.add(
            RemediationExecution(
                idempotency_key=key,
                thread_id=thread_id,
                action_type=action_type,
                target=target,
                params=params,
                result=result,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.commit()
        return result, False
    finally:
        session.close()
