"""Service-scoped memory of confirmed incident diagnoses (v0.4 Phase 1).

Two disciplines this module exists to enforce in code, not in a prompt:

1. Write policy — `write_confirmed_memory` is the *only* path onto
   `service_memory`, and it writes only from the structured
   `InvestigationResult`/`SubmitDiagnosisArgs` objects, never from raw model
   text. It refuses to write for an unconfirmed ('escalate') or
   low-confidence diagnosis: memory this system "knows" must mean something.
2. Scoping — every read here takes a `service_id` and filters on it. There
   is no function in this module that can return another service's rows;
   that's what makes the cross-service isolation test in
   tests/test_memory.py a structural guarantee, not a hopeful one.

Retrieval (v0.4 Phase 2) adds a third discipline, the contamination
guardrail: `retrieve_relevant_memory`/`format_memory_for_prompt` surface
prior incidents to the model as clearly-labeled advisory context, never as
something that can override contradicting current evidence. See
`format_memory_for_prompt`'s docstring for the exact framing, and
`opspilot.agent.support.system_prompt` for where it lands.
"""

import datetime as dt
import logging

from sqlalchemy.orm import Session

from opspilot.agent.schemas import InvestigationResult
from opspilot.config import get_settings
from opspilot.models import ServiceMemory

log = logging.getLogger("opspilot.memory")

# How many prior incidents get surfaced to the model per investigation. Kept
# small and deliberately not a full corpus dump — this is a hint, not a
# knowledge base the model is expected to search.
MEMORY_CONTEXT_LIMIT = 3


def _derive_outcome(result: InvestigationResult) -> str:
    """Derived from the policy verdict and (if any) the approval decision —
    never from the model's own account of what happened. ESCALATE verdicts
    never reach here: write_confirmed_memory already refuses to write
    before this is ever called, since 'escalate' recommendations are the
    one action_type that maps to ESCALATE (opspilot.agent.policy)."""
    verdict = result.policy_verdict
    if verdict == "BLOCK":
        return "blocked"
    if verdict == "EXECUTE":
        return "executed"
    if verdict == "REQUIRE_APPROVAL":
        approved = bool((result.approval_decision or {}).get("approved"))
        return "approved_and_executed" if approved else "denied"
    return "unknown"


def write_confirmed_memory(
    db: Session, *, service_id: int, incident_id: int, result: InvestigationResult
) -> ServiceMemory | None:
    """The single write path onto `service_memory`. Call once, at incident
    close. Returns None (writes nothing) for anything that isn't a
    confirmed, sufficiently-confident diagnosis — the write-policy
    discipline the blueprint requires: nothing is written for
    escalated/inconclusive incidents or for diagnoses below the configured
    confidence floor."""
    diagnosis = result.diagnosis
    if diagnosis is None:
        return None
    if diagnosis.recommended_action == "escalate":
        return None
    min_confidence = get_settings().memory_write_min_confidence
    if diagnosis.confidence < min_confidence:
        return None

    memory = ServiceMemory(
        service_id=service_id,
        source_incident_id=incident_id,
        symptom_pattern="; ".join(diagnosis.evidence),
        root_cause=diagnosis.diagnosis,
        fix_applied=diagnosis.recommended_action,
        outcome=_derive_outcome(result),
        confidence=diagnosis.confidence,
        created_at=dt.datetime.now(dt.UTC),
    )
    db.add(memory)
    db.flush()
    log.info(
        "service memory written",
        extra={
            "extra_fields": {
                "service_id": service_id,
                "incident_id": incident_id,
                "memory_id": memory.id,
                "fix_applied": memory.fix_applied,
                "outcome": memory.outcome,
            }
        },
    )
    return memory


def list_service_memory(db: Session, service_id: int) -> list[ServiceMemory]:
    """Scoped read — the only kind this module offers. Every caller,
    including the admin endpoint and Phase 2's retrieval step, goes through
    this (or an equally service_id-filtered query) rather than querying
    ServiceMemory directly."""
    return (
        db.query(ServiceMemory)
        .filter_by(service_id=service_id)
        .order_by(ServiceMemory.created_at.desc())
        .all()
    )


def retrieve_relevant_memory(
    db: Session, service_id: int, *, limit: int = MEMORY_CONTEXT_LIMIT
) -> list[ServiceMemory]:
    """The read side of retrieval: the rows most worth surfacing for this
    service, ranked by how many times a pattern has been confirmed, then
    confidence, then recency. Exact/keyword ranking is deliberate, not a
    placeholder — add embeddings only once eval data shows this actually
    under-retrieves at a larger corpus size, not in advance (see
    consolidate_service_memory's docstring for the same pgvector-deferral
    reasoning). Scoped by service_id like every other read in this module."""
    return (
        db.query(ServiceMemory)
        .filter_by(service_id=service_id)
        .order_by(
            ServiceMemory.occurrence_count.desc(),
            ServiceMemory.confidence.desc(),
            ServiceMemory.created_at.desc(),
        )
        .limit(limit)
        .all()
    )


def format_memory_for_prompt(rows: list[ServiceMemory]) -> str:
    """Renders retrieved memory as a labeled, advisory block for the system
    prompt — the contamination guardrail in text form. This is deliberately
    explicit rather than trusting the model to infer the right weight to
    give old data: prior incidents can *suggest* a hypothesis, they can
    never outrank what the current investigation's tools actually show.
    Returns "" (nothing to append) when there's no memory to surface."""
    if not rows:
        return ""
    lines = [
        "Prior related incidents for this service (advisory only, from past "
        "confirmed diagnoses — NOT current evidence). These may suggest a "
        "hypothesis worth checking first, but if what you actually observe "
        "in this incident's metrics, logs, deployments, or dependency "
        "status contradicts one of these, trust the current evidence, not "
        "this list:"
    ]
    for row in rows:
        lines.append(
            f"- Symptom pattern: {row.symptom_pattern}\n"
            f"  Root cause: {row.root_cause}\n"
            f"  Fix applied: {row.fix_applied} (outcome: {row.outcome})\n"
            f"  Confirmed {row.occurrence_count}x, confidence {row.confidence:.2f}"
        )
    return "\n".join(lines)


def consolidate_service_memory(db: Session, service_id: int) -> int:
    """Merges near-duplicate memory rows for one service into a single row,
    so repeatedly confirming the same incident pattern strengthens one
    memory (via `occurrence_count`) instead of piling up identical ones.

    "Near-duplicate" is exact-match on (symptom_pattern, fix_applied): at
    this corpus size that's precise enough — see the blueprint's pgvector
    deferral (decide with eval data once keyword/exact matching actually
    under-retrieves, not in advance). Returns the number of rows removed.
    """
    rows = list_service_memory(db, service_id)
    groups: dict[tuple[str, str], list[ServiceMemory]] = {}
    for row in rows:
        groups.setdefault((row.symptom_pattern, row.fix_applied), []).append(row)

    removed = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        # list_service_memory orders newest-first; the survivor is the most
        # recent confirmation of this pattern.
        survivor, *stale = group
        survivor.occurrence_count = sum(row.occurrence_count for row in group)
        survivor.confidence = max(row.confidence for row in group)
        for row in stale:
            db.delete(row)
            removed += 1
    db.flush()
    return removed
