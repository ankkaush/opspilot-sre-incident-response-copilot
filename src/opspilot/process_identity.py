"""Process identity (v0.5 Phase 3).

A single random id generated once, at import time — which in practice
means once per process lifetime; a restart always produces a new one.

Durable checkpointing (v0.5 Phase 1) and idempotent remediation (v0.5
Phase 2) both make "this incident's pause and resume survived a process
restart" *true*, but neither makes it *visible* — resuming looks
identical whether the process restarted in between or not, which is the
whole point of durability, but leaves the hardening phase's "a
demonstrable resumed-after-crash timeline entry" with nothing to point at.
Stamping this id onto the "approval requested" and "approval decided"
audit rows (see opspilot.routers.incidents) is what closes that gap: two
different values on the same incident is concrete, queryable evidence a
process restart actually happened in between — not a demo narrating that
it did.
"""

import uuid

PROCESS_INSTANCE_ID: str = str(uuid.uuid4())
