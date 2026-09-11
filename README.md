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

**Status:** v0.1 Phase 2 — Tool Layer & the Raw Agent Loop.

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
- **A raw agent loop** (`opspilot.agent.loop.investigate`) — no framework:
  an explicit call-model → parse tool call → validate → execute → repeat
  cycle over five read-only tools (`get_metrics`, `get_logs`,
  `get_recent_deployments`, `get_dependency_status`, `get_runbook`), each
  scoped in code to one scenario so the model can never pull evidence from a
  different incident. The final answer is itself a validated tool call
  (`submit_diagnosis`), not parsed free text. A hard-coded max-steps and
  max-cost-per-run ceiling stops a misbehaving loop — that ceiling is
  enforced by code, never left to the model's own judgment.

There is **no remediation and no policy engine yet** — every tool is a pure
read. That's v0.2, once the policy layer exists to gate state-changing
actions. There is also no persisted incident/audit trail yet — that's v0.1
Phase 3.

## Running an investigation

Requires an `ANTHROPIC_API_KEY` in `.env` (not needed for Phase 1's
inspection endpoints or for the test suite, which scripts a fake model
client and never calls the real API):

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
cp .env.example .env   # edit API_KEY to something real
docker compose up --build
```

This runs migrations, seeds the two synthetic scenarios (idempotently — safe
to restart), and starts the API on `http://localhost:8000`.

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: <your API_KEY>" http://localhost:8000/api/v1/scenarios
curl -H "X-API-Key: <your API_KEY>" http://localhost:8000/api/v1/scenarios/checkout-deploy-outage
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
export DATABASE_URL=postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot
export API_KEY=test-key
export CORS_ORIGINS=http://localhost:3000
alembic upgrade head
pytest -q
```

`test_migrations.py` deliberately downgrades and re-upgrades the schema as
part of the round-trip check — don't point it at a database you care about.

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
  agent/loop.py        The raw agent loop (no framework)
  agent/schemas.py     Diagnosis output, evidence trail, investigation result
alembic/               Migrations
tests/                 Auth, migration round-trip, seed determinism,
                       tool unit tests, scripted agent-loop tests
```
