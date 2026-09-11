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

**Status:** v0.2 Phase 1 — LangGraph Migration. v0.1 (raw loop, tools, incident API, dashboard) is complete and frozen; v0.2 replaces the control flow with an explicit state machine.

## What exists right now

- FastAPI app with real API-key authentication on every route except `/health`
- PostgreSQL schema (via Alembic) for the synthetic environment: services,
  scenarios, deployments, metrics, logs, dependency statuses, runbooks
- A deterministic seed generator — two hand-built incident scenarios, each
  carrying a structured **ground-truth block** (injected cause, expected
  evidence, expected diagnosis, expected action, expected policy verdict).
  Nothing scores against this yet (that's v0.3) — it's defined now because
  writing it after the fact, once more scenarios exist, is expensive.
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
  lines inside one function. Two new nodes reached once a diagnosis exists:
  `hypothesize` deterministically checks whether the diagnosis's cited
  evidence was actually gathered (a hallucination guard, not a semantic
  check), and `classify_risk` previews the risk-tier table the real v0.2
  Phase 2 policy engine will formalize and actually enforce — informational
  only for now, since there's nothing to gate until remediation tools exist.
  Same max-steps and max-cost-per-run ceilings as v0.1, still enforced by
  code, never left to the model's own judgment.

There is **no remediation and no policy engine yet** — every tool is still a
pure read, and there's nothing for a policy engine to gate. That's v0.2
Phase 2. There is also no human-in-the-loop, memory, tracing, or eval yet.

- **A real Incident API** (`opspilot.routers.incidents`): create an Incident
  against a seeded Scenario, run it (calls the agent loop and persists every
  tool call as an append-only audit-log row), fetch it, list all of them, or
  fetch its timeline. Re-running an already-run Incident is refused (409) —
  a deterministic rule, not a suggestion, matching the same "code decides"
  principle as everything else here.
- **An append-only audit trail**: every tool call the agent makes during a
  run — including a rejected/invalid one — becomes its own `AuditLogEntry`
  row, in order, linked to the Incident. Nothing here is ever updated or
  deleted.
- **Security**: per-API-key rate limiting (in-memory, single-process — see
  the comment in `auth.py` for why that's the right amount of complexity
  here) and a request-body-size limit, both new Phase 3 requirements, plus
  input validation on incident creation (scenario key pattern + existence
  check).
- **A minimal dashboard** (`web/`, Next.js App Router): an incident list
  with a "create & run" form, and an incident detail page rendering the
  actual timeline — *incident started → evidence gathered (one entry per
  tool call) → diagnosis formed → final status* — with the diagnosis and
  cited evidence up top. No charts, no analytics; it's a real, honest record
  of what the agent did, which is the whole Phase 3 dashboard requirement.
  The API key is read only in server components/actions and never reaches
  the browser bundle — see `web/lib/api.ts`.

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

## Running it

```bash
cp .env.example .env   # edit API_KEY and ANTHROPIC_API_KEY
docker compose up --build
```

This runs migrations, seeds the two synthetic scenarios (idempotently — safe
to restart), and starts the API on `http://localhost:8000`.

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: <your API_KEY>" http://localhost:8000/api/v1/scenarios

curl -X POST -H "X-API-Key: <your API_KEY>" -H "Content-Type: application/json" \
  -d '{"scenario_key": "checkout-deploy-outage"}' \
  http://localhost:8000/api/v1/incidents

curl -X POST -H "X-API-Key: <your API_KEY>" \
  http://localhost:8000/api/v1/incidents/1/run

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
  agent/nodes.py       gather_context, hypothesize, classify_risk, decide
  agent/graph.py       Builds the LangGraph state machine, runs it
  agent/loop.py        Public investigate() entrypoint (delegates to graph.py)
  agent/schemas.py     Diagnosis output, evidence trail, investigation result
  routers/incidents.py Incident CRUD, run endpoint, timeline endpoint
alembic/               Migrations
tests/                 Auth, migration round-trip, seed determinism,
                       tool unit tests, node-level unit tests, full-graph
                       integration tests, end-to-end incident tests
                       (create/run/timeline, rate limiting, body size limits)
web/                   Minimal Next.js dashboard (incident list + timeline)
```
