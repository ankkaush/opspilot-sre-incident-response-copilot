# OpsPilot — SRE Incident Response Copilot

An AI agent that investigates incidents in a small, deterministic synthetic SRE
environment, forms an evidence-grounded diagnosis, and recommends a remediation
that a deterministic policy layer — not the model — decides whether to execute,
gate behind human approval, or block outright.

This is an AI-engineering portfolio project built in five versions, each
extending the last rather than replacing it: **raw tool-calling agent → reliable
/ controlled agent → measurable agent → context-aware agent → durable agent**.
The full phase-by-phase plan lives in the engineering blueprint (not checked
into this repo).

**Status:** v0.4 complete — Context-Aware Agent. v0.1–v0.3 are frozen. Phase 1 built service-scoped memory's schema and write policy; this phase (Phase 2) wires retrieval into `gather_context` as advisory context, adds a per-service memory dashboard, and proves — with a reproducible, zero-cost eval-harness comparison — that memory actually reduces the tool calls needed to reach the same correct diagnosis on a repeat-pattern incident.

## What exists right now

- FastAPI app with real API-key authentication on every route except `/health`
- PostgreSQL schema (via Alembic) for the synthetic environment: services,
  scenarios, deployments, metrics, logs, dependency statuses, runbooks
- **A 17-scenario golden dataset** (`opspilot.seed.scenarios`), the deterministic
  seed generator's full payload as of v0.3 Phase 1 — each scenario carrying a
  structured **ground-truth block** (injected cause, expected evidence,
  expected diagnosis, expected action, expected policy verdict), reviewable
  by a human without ever running the agent. Covers every policy verdict at
  least once (`EXECUTE`, `REQUIRE_APPROVAL`, `BLOCK`, `ESCALATE`) across
  three services (checkout-api, payments-api, inventory-api):
  deployment-caused and non-deployment-caused incidents with the same
  surface symptom, dependency failures, resource exhaustion (CPU, memory,
  and a stuck-process variant that looks like resource exhaustion but
  isn't), deliberately misleading correlations (a coincidentally-timed but
  unrelated deploy; a feature-flag rollout that looks like a capacity
  issue), incomplete/ambiguous evidence, an already-self-resolved blip, and
  one action (`delete_data`) the policy engine must always `BLOCK` — added
  specifically because nothing in the existing action vocabulary could
  reach that verdict before this phase (see `opspilot.agent.policy`).
- **The evaluation harness** (`opspilot.eval`) — runs every scenario through
  the exact same `investigate()` entrypoint the real Incident API uses (no
  eval-mode bypass of the policy engine) and scores each run two ways:
  - **Deterministic** (`opspilot.eval.deterministic`, no model call): did
    the policy verdict match ground truth, did the recommended action match
    exactly, which fraction of the expected tool categories actually got
    called, how many tool calls were unnecessary or errored, steps, cost,
    tokens, latency.
  - **LLM-as-judge** (`opspilot.eval.judge`, one structured-output model
    call per scenario): diagnosis accuracy, evidence groundedness (the
    RAGAS-style faithfulness idea, borrowed as a rubric rather than
    imported as a dependency), remediation quality, and escalation
    correctness — four independent scores, never blended into one number.

  `opspilot.eval.regression` compares two saved runs and flags any metric
  that moved past a threshold — the "did this prompt change make things
  worse" check, proven reproducibly in `tests/test_eval_regression.py`
  with synthetic before/after runs rather than relying on live model
  variance between two paid runs. Results save as JSON under `eval_runs/`
  (gitignored — regenerable output, not a source artifact). Run it with
  `python -m opspilot.eval` (see below).
- Read-only inspection endpoints (`/api/v1/services`, `/api/v1/scenarios`,
  `/api/v1/scenarios/{key}`) so the seeded data is verifiable over HTTP
- Structured JSON logging, Docker Compose, GitHub Actions CI
- **A LangGraph state machine** (`opspilot.agent.graph.run_investigation`,
  reached through the unchanged `opspilot.agent.loop.investigate` entrypoint)
  over the same five read-only tools from v0.1 (`get_metrics`, `get_logs`,
  `get_recent_deployments`, `get_dependency_status`, `get_runbook`), each
  still scoped in code to one scenario. `gather_context` makes one model
  call and one round of tool execution, self-looping via a conditional edge
  until the model calls `submit_diagnosis` or a deterministic ceiling is
  hit — the same control flow v0.1's while-loop had, now expressed as named,
  independently-callable, independently-testable graph nodes instead of
  lines inside one function. Two nodes reached once a diagnosis exists:
  `hypothesize` deterministically checks whether the diagnosis's cited
  evidence was actually gathered (a hallucination guard, not a semantic
  check), and `classify_risk` computes an informational risk-tier preview.
  Same max-steps and max-cost-per-run ceilings as v0.1, still enforced by
  code, never left to the model's own judgment.
- **The deterministic policy engine** (`opspilot.agent.policy`) — the real
  gate: `evaluate(ProposedAction) -> EXECUTE | REQUIRE_APPROVAL | BLOCK |
  ESCALATE`, keyed *only* on `action_type`, never on diagnosis text or
  confidence. An unrecognized action type fails closed (`BLOCK`), not open.
  Reached by a new `evaluate_policy` graph node between `classify_risk` and
  `decide`. Proven adversarially: a diagnosis engineered to sound maximally
  confident and urgent about a gated action still gets `REQUIRE_APPROVAL`
  (`tests/test_policy.py::test_verdict_is_independent_of_target_and_params`).
- **Simulated remediation tools** (`opspilot.agent.remediation_tools`):
  `rollback_deployment`, `restart_service`, `scale_service`,
  `toggle_feature_flag` — schema-validated and parameterized, never a
  free-text or shell action. Deliberately **not** exposed to the model as
  callable tools during `gather_context` — the read-only tool set stays
  read-only, exactly as in v0.1. Only `evaluate_policy` ever calls one of
  these, and only after a verdict of `EXECUTE`. Today that's just
  `restart_service`/`scale_service` (the two `EXECUTE`-tier actions with no
  extra parameters to collect) — `rollback_deployment` and
  `toggle_feature_flag` are gated `REQUIRE_APPROVAL` and, as of this phase,
  really do pause for a human rather than silently going nowhere.
- **Real human-in-the-loop** — `evaluate_policy` calls LangGraph's
  `interrupt()` on a `REQUIRE_APPROVAL` verdict, pausing the graph mid-node.
  A process-lifetime `InMemorySaver` checkpointer (`opspilot.agent.graph`)
  makes that pause resumable from an entirely different HTTP request, with a
  fresh DB session and a fresh compiled graph object — only the checkpointer
  instance and a stable `thread_id` (`incident-{id}`) need to carry over.
  Deliberately in-memory, not database-backed: real pause/resume within one
  running process, but a restart loses anything paused — durable,
  restart-surviving checkpointing is v0.5's job, not v0.2's.
  `POST /incidents/{id}/approvals` is the only way to resolve a pause; an
  approval carries concrete parameters (a rollback's target version, a
  flag's name) validated against the same remediation-tool schema before
  the graph is ever resumed. A paused incident nobody decides on in time
  auto-escalates via a lazy SLA-timeout check (no background scheduler —
  checked whenever anything next reads the incident).
- **Reliability**: a model call that exhausts `call_with_retries`' bounded
  retries ends the investigation in a defined `incomplete_provider_error`
  state — a graceful give-up, not a crash.
- **A real Incident API** (`opspilot.routers.incidents`): create an Incident
  against a seeded Scenario, run it (calls the agent loop and persists every
  tool call as an append-only audit-log row), approve or deny a pending
  action, fetch it, list all of them, or fetch its timeline. Re-running an
  already-run Incident is refused (409) — a deterministic rule, not a
  suggestion, matching the same "code decides" principle as everything else
  here.
- **An append-only audit trail**: every tool call, approval request, and
  approval decision becomes its own `AuditLogEntry` row, in order, linked to
  the Incident, with the approving actor's identity recorded. Nothing here
  is ever updated or deleted.
- **Security**: per-API-key rate limiting (in-memory, single-process — see
  the comment in `auth.py` for why that's the right amount of complexity
  here) and a request-body-size limit, plus input validation on incident
  creation and on approval parameters.
- **A dashboard** (`web/`, Next.js App Router): an incident list with a
  "create & run" form and a pending-approvals queue; an incident detail page
  showing a graph-state row (which node the incident is conceptually sitting
  in), a live approval form when one's pending, the diagnosis, and the full
  timeline — *incident started → evidence gathered → diagnosis formed →
  approval requested → approval decided → final status*. No charts, no
  analytics; a real, honest record of what happened. The API key is read
  only in server components/actions and never reaches the browser bundle —
  see `web/lib/api.ts`.
- **Langfuse tracing** (`opspilot.agent.tracing`) — one trace per
  `run_investigation`/`resume_investigation` call, with a nested span per
  graph node, a `generation` observation per model call (input messages,
  output content, token usage, cost), and a `tool` observation per tool
  invocation (including error paths), all tagged with `graph_version` and
  `prompt_version` metadata so a scorecard change can be attributed to the
  exact code that produced it. Tracing is observability, never control: the
  Langfuse client no-ops gracefully whenever `LANGFUSE_PUBLIC_KEY`/
  `LANGFUSE_SECRET_KEY` aren't set, so an unconfigured account changes
  visibility, never behavior. An explicit key-based redaction pass
  (`mask=` on the Langfuse client) strips anything secret-shaped
  (`api_key`, `authorization`, `password`, `token`, ...) from every payload
  before it leaves the process. Every `Incident` persists the trace id of
  its most recent run/resume, exposed as a "View trace" link on the
  incident detail page; a resume gets its own trace (it may run in an
  entirely different process, arbitrarily later) sharing the same
  `thread_id` in its metadata so the pair is still findable in the
  Langfuse UI.
- **An agent-facing eval dashboard** (`/eval`, `/eval/[label]`) reading the
  eval harness's saved JSON runs (`opspilot.routers.eval_runs`, read-only —
  eval runs stay files under `eval_runs/`, never a DB table): a run list
  with pass/fail-shaped metrics and cost/latency, and a per-run detail page
  with the full scorecard plus a per-scenario table (policy-verdict
  correctness, action exact-match, judge diagnosis score) and a "View
  trace" link straight into the corresponding Langfuse trace — the
  drill-through from score to reasoning the blueprint's "done when" for
  this phase asks for.
- **Service-scoped memory of confirmed diagnoses** (`opspilot.memory`,
  `service_memory` table) — v0.4. Every incident close runs through one
  write-policy gate (`write_confirmed_memory`): a memory row is written
  only for a confirmed diagnosis (never for `recommended_action ==
  "escalate"`, the model's own "I'm not confident enough" signal) at or
  above a configurable confidence floor (`MEMORY_WRITE_MIN_CONFIDENCE`,
  default 0.6) — never from raw model chatter, only from the structured
  `SubmitDiagnosisArgs`/`InvestigationResult` objects. `outcome` is derived
  from the policy verdict and (if any) approval decision, never from what
  the model claims happened. A same-service consolidation pass
  (`consolidate_service_memory`) merges exact-duplicate patterns (same
  symptom evidence, same fix) into one row with a growing
  `occurrence_count`, rather than letting repeat confirmations pile up.
  Every read is scoped by `service_id` — there is no function in
  `opspilot.memory` that can return another service's rows (proven
  adversarially in `tests/test_memory.py`). A minimal admin endpoint
  (`DELETE /api/v1/memory/{id}`) lets a wrong entry be removed.
- **Retrieval, wired into the real investigation** (`opspilot.memory.
  retrieve_relevant_memory`/`format_memory_for_prompt`, `opspilot.agent.
  support.system_prompt`) — Phase 2. Once per investigation (not once per
  `gather_context` self-loop iteration), the top few memory rows for the
  incident's service — ranked by how many times a pattern's been confirmed,
  then confidence, then recency — are rendered as a clearly-labeled "Prior
  related incidents" block and appended to the system prompt. The
  contamination guardrail is explicit in that text, not just in code: prior
  incidents may suggest a hypothesis, but the model is told outright to
  trust current evidence over memory whenever the two disagree.
  `memory_enabled=False` (threaded through `investigate`/
  `run_investigation`/`run_scenario`/`run_eval`, and `--no-memory` on the
  eval CLI) is the on/off lever for measuring retrieval's own impact — real
  incidents always leave it at the default.
- **Measured impact, reproducibly, at zero cost** (`tests/
  test_memory_eval_impact.py`) — the v0.3 eval harness, unchanged, run
  twice against a golden scenario primed with its own confirmed diagnosis
  as memory (a repeat pattern): once with retrieval on, once off, driven by
  a scripted chat function that deterministically takes fewer confirmatory
  tool calls when the memory block is present in its system prompt and a
  full evidence sweep when it isn't — both converging on the identical,
  correct diagnosis. `mean_steps_used` drops from 5 to 2 with memory
  enabled, `recommended_action_exact_match_rate` and `policy_verdict_
  accuracy` stay at 1.0 either way: memory changes efficiency, not
  correctness, and the comparison is exact and reproducible in CI rather
  than resting on live model variance between two paid runs.
- **A per-service memory dashboard** (`/services/[name]/memory`) — what
  OpsPilot has confirmed about a service (symptom pattern, root cause, fix,
  outcome, confidence, how many times confirmed, when, from which
  incident), linked from the home page per service, with a Delete action
  per row wired to the Phase 1 admin endpoint for correcting a wrong entry.

## Running an investigation

Requires an `ANTHROPIC_API_KEY` in `.env` (not needed for Phase 1/2's
inspection endpoints or for the test suite, which scripts a fake model
client and never calls the real API). The dashboard is the normal way to do
this now; direct Python is still useful for scripting or debugging:

```python
from opspilot.db import SessionLocal
from opspilot.models import Scenario
from opspilot.agent.loop import investigate

session = SessionLocal()
scenario = session.query(Scenario).filter_by(key="checkout-deploy-outage").one()
result = investigate(session, scenario)

print(result.status)              # "diagnosed"
print(result.diagnosis)           # SubmitDiagnosisArgs(...)
print(result.evidence_trail)      # every tool call made, in order
print(result.estimated_cost_usd)
```

## Running the eval harness

Also requires `ANTHROPIC_API_KEY`. Scope which scenarios run with
`--scenarios` to control cost — a full 17-scenario run with judging costs
roughly the same order of magnitude as two dozen ordinary investigations
(a 3-scenario smoke test with judging cost about $0.03 in testing):

```bash
python -m opspilot.eval --scenarios checkout-deploy-outage,payments-db-latency --label baseline
python -m opspilot.eval --scenarios checkout-deploy-outage,payments-db-latency --label candidate --compare-to baseline
python -m opspilot.eval --no-judge     # deterministic checks only, still real agent calls
python -m opspilot.eval --no-memory    # v0.4: disable memory retrieval for this run
```

Prints a scorecard, saves the full result to `eval_runs/<label>.json`, and
— with `--compare-to` — a regression report against a previously saved run.

## Tracing (optional)

Add `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (and `LANGFUSE_BASE_URL` if
your Langfuse account isn't on the default US-less cloud region — see
`.env.example`) to trace every investigation and eval run. Leaving them
unset is fully supported: the agent runs identically, it's just invisible
to Langfuse. With them set, every `Incident` and eval-scenario result
carries a clickable trace URL — visible on the incident detail page and the
`/eval/[label]` scorecard — down to the individual model and tool calls
that produced it.

## Running it

```bash
cp .env.example .env   # edit API_KEY and ANTHROPIC_API_KEY
docker compose up --build
```

This runs migrations, seeds the 17 golden scenarios (idempotently — safe to
restart), and starts the API on `http://localhost:8000`.

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: <your API_KEY>" http://localhost:8000/api/v1/scenarios

curl -X POST -H "X-API-Key: <your API_KEY>" -H "Content-Type: application/json" \
  -d '{"scenario_key": "checkout-deploy-outage"}' \
  http://localhost:8000/api/v1/incidents

curl -X POST -H "X-API-Key: <your API_KEY>" \
  http://localhost:8000/api/v1/incidents/1/run

# If that pauses (status: "awaiting_approval"), resolve it:
curl -X POST -H "X-API-Key: <your API_KEY>" -H "Content-Type: application/json" \
  -d '{"approved": true, "actor": "alice", "params": {"target_version": "v2.7"}}' \
  http://localhost:8000/api/v1/incidents/1/approvals

curl -H "X-API-Key: <your API_KEY>" http://localhost:8000/api/v1/incidents/1/timeline
```

To run the dashboard against it:

```bash
cd web
cp .env.example .env.local   # OPSPILOT_API_KEY must match the backend's API_KEY
npm install
npm run dev
```

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# start Postgres only
docker compose up -d db

cp .env.example .env   # edit as needed
export $(grep -v '^#' .env | xargs)   # or use direnv / your own tooling

alembic upgrade head
python -m opspilot.seed
uvicorn opspilot.main:app --reload
```

## Tests

Tests need a running Postgres reachable via `DATABASE_URL`, and `API_KEY` set
in the environment (tests assert against whatever key is actually configured
— there's no hardcoded test key baked into application code).

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://opspilot:local-dev-only-not-a-secret@localhost:5432/opspilot
export API_KEY=test-key
export CORS_ORIGINS=http://localhost:3000
alembic upgrade head
pytest -q
```

`test_migrations.py` deliberately downgrades and re-upgrades the schema as
part of the round-trip check — don't point it at a database you care about.

## Secrets & security

This repository is public. The rules that keeps it safe to be public:

- `.env` is gitignored and must never be committed. `.env.example` is the
  only committed env file, and every value in it is a placeholder — never a
  real credential.
- Nothing in source code, Docker files, CI config, tests, or docs contains a
  real secret. Where a container or CI job needs *some* credential value
  (the local Postgres container, the CI Postgres service), it's an obviously
  fake, human-readable placeholder scoped to that ephemeral container —
  never reused anywhere real.
- CI runs [gitleaks](https://github.com/gitleaks/gitleaks) on every push and
  PR to catch anything that looks like a committed secret before it lands.
- The dashboard (`web/`) reads its API key server-side only
  (`OPSPILOT_API_KEY`, deliberately not `NEXT_PUBLIC_`-prefixed) — it never
  reaches the browser bundle. See `web/lib/api.ts`.
- When a new phase introduces a new external service or API, `.env.example`
  gets a new placeholder entry in the same commit — never the real value.

If you ever find a real secret in this repo, treat it as already
compromised: rotate it immediately, then remove it.

## Project layout

```
src/opspilot/
  main.py              FastAPI app, CORS, error handling, /health
  config.py            Pydantic Settings (env-driven, no hardcoded secrets)
  auth.py              X-API-Key dependency, constant-time comparison
  db.py                SQLAlchemy engine/session
  models.py            Synthetic-environment ORM models
  schemas.py           Pydantic response models
  logging_config.py    Structured JSON logging
  routers/inspect.py   Read-only inspection endpoints
  seed/scenarios.py    Deterministic scenario definitions + ground truth
  seed/generator.py    Pure payload builder + idempotent DB seeding
  agent/tools.py       Five read-only tools, scoped per scenario
  agent/client.py      Provider-agnostic model call abstraction + retries
  agent/support.py     Shared prompt/serialization helpers (nodes + loop)
  agent/state.py       GraphState schema, NodeDeps, initial_state()
  agent/policy.py      The deterministic policy engine (evaluate())
  agent/remediation_tools.py  Simulated, parameterized remediation actions
  agent/nodes.py       gather_context, hypothesize, classify_risk,
                       evaluate_policy, decide
  agent/graph.py       Builds the LangGraph state machine, runs it
  agent/loop.py        Public investigate() entrypoint (delegates to graph.py)
  agent/schemas.py     Diagnosis output, evidence trail, investigation result
  agent/tracing.py     Langfuse tracing: redaction, trace/span/generation/
                       tool-call context managers, trace URL lookup
  routers/incidents.py Incident CRUD, run/approvals endpoints, timeline
  routers/eval_runs.py Read-only API over eval_runs/*.json for the dashboard
  memory.py             Service-scoped memory: write policy, consolidation,
                        scoped retrieval, prompt formatting (opspilot.memory)
  routers/memory.py     GET .../memory (dashboard) + DELETE /api/v1/memory/{id}
  eval/deterministic.py Code-only scoring: policy/action match, tool
                       selection, errors, cost, tokens, latency
  eval/judge.py        LLM-as-judge: diagnosis accuracy, evidence
                       groundedness, remediation quality, escalation
  eval/runner.py       Runs the golden dataset through investigate(),
                       scores each run, aggregates
  eval/regression.py   Flags metrics that regressed between two saved runs
  eval/scorecard.py    Human-readable scorecard rendering
  eval/storage.py      Saves/loads EvalRuns as JSON
  eval/__main__.py     CLI: python -m opspilot.eval
alembic/               Migrations
tests/                 Auth, migration round-trip, seed determinism +
                       golden-dataset coverage checks (verdict coverage,
                       reviewable ground truth, dataset size), tool unit
                       tests, node-level unit tests, full-graph integration
                       tests (including the full HITL pause/resume flow,
                       provider-failure injection, and the BLOCK path),
                       end-to-end incident tests (create/run/approve,
                       rate limiting, body size limits, SLA timeout),
                       policy engine tests, remediation tool tests, eval
                       harness tests (deterministic scoring, judge parsing,
                       full runner, regression detection, storage), tracing
                       tests (redaction, serialization, no-op-when-
                       unconfigured behavior), service-memory tests
                       (write-policy gate, outcome derivation, consolidation,
                       cross-service isolation, retrieval ranking, read/admin
                       endpoints), the memory-impact eval-harness comparison
                       (test_memory_eval_impact.py)
web/                   Next.js dashboard (incident list, pending-approvals
                       queue, graph-state view, approval form, timeline,
                       eval run list + per-run scorecard with trace links,
                       per-service memory view with delete/correction)
eval_runs/             Eval results (gitignored — regenerable output)
```
