# OpsPilot — SRE Incident Response Copilot

An AI agent that investigates production-style incidents in a small, deterministic synthetic SRE environment: it gathers evidence across metrics, logs, deployments, and dependency status, forms an evidence-grounded diagnosis, and recommends a remediation that a deterministic policy layer — never the model — decides to execute automatically, hold for human approval, or block outright. The whole lifecycle survives a real process crash: a paused approval, and the remediation it eventually triggers, resume from exactly where they stopped and never execute twice.

## Why this exists

Most "AI SRE agent" demos stop at `symptom → plausible-sounding fix`. That's not the same problem as `symptom → evidence-grounded diagnosis → a remediation that only runs if a deterministic policy allows it → survives a crash without ever double-executing` — the actual gap between a model that sounds confident and a system an operator could trust with real infrastructure. A model recommending `rollback_deployment` with 0.95 confidence is not the same thing as that rollback being safe to run unattended; this project treats the boundary between "the model proposes" and "code decides" as the actual product, not a footnote, and extends that same rigor to what happens when the process running it crashes mid-approval — a condition most agent demos never simulate, let alone survive correctly.

## Status

| Capability | Status |
|---|---|
| Synthetic SRE environment + 17-scenario golden dataset with ground truth | ✅ Complete — tested |
| Raw tool-calling agent loop (LangGraph state machine, 5 read-only tools) | ✅ Complete — tested |
| Deterministic policy engine (`EXECUTE` / `REQUIRE_APPROVAL` / `BLOCK` / `ESCALATE`) | ✅ Complete — tested, including adversarial cases (a maximally-confident diagnosis still can't talk its way past the gate) |
| Human-in-the-loop approval (pause / resume) | ✅ Complete — tested; live-verified end to end via the dashboard |
| Durable, Postgres-backed checkpointing (crash-and-resume) | ✅ Complete — tested via a kill-and-resume harness at every node boundary, plus the real production HTTP path |
| Idempotent remediation execution | ✅ Complete — tested at the node-re-entry level and via audit-trail visibility; a crash-and-resume can never execute a remediation twice |
| Evaluation harness (deterministic + LLM-as-judge) | ✅ Complete — tested; live-verified with real Claude calls |
| Langfuse tracing | ✅ Complete — live-verified against real Langfuse Cloud |
| Service-scoped memory (write policy, retrieval, measured impact) | ✅ Complete — tested; impact measured reproducibly at zero live cost (`mean_steps_used` 5 → 2 with memory on) |
| Dashboard (Next.js) — incident lifecycle, durable workflows, memory, eval | ✅ Complete — every page live/dynamic against the real backend, verified in a browser |
| Deterministic test suite | ✅ **202/202 passing** |

Every row above marked "tested" is covered by the automated suite (`pytest -q`, no API key required — every test scripts a fake model client). Rows marked "live-verified" additionally had a real Claude and/or Langfuse call made against them at least once during development, on top of the deterministic tests.

## How it works

```
Incident created (against a seeded synthetic scenario)
      ↓
gather_context     one model call + one tool round, self-loops until the
                   model calls submit_diagnosis or a hard step/cost ceiling
                   is hit (code-enforced, never the model's own judgment)
      ↓
hypothesize        deterministic: was the diagnosis's cited evidence
                   actually gathered? (a hallucination guard)
      ↓
classify_risk      informational risk-tier preview
      ↓
evaluate_policy    THE decision — keyed only on action type, never on
                   diagnosis text or the model's stated confidence
      │
      ├─ EXECUTE ──────────→ remediation runs automatically
      │                      (idempotency-guarded — see below)
      ├─ REQUIRE_APPROVAL ─→ graph pauses (interrupt), durably checkpointed
      │                      to Postgres; waits for POST /approvals
      ├─ BLOCK ────────────→ refused outright, nothing executes, ever
      └─ ESCALATE ─────────→ handed to a human, no action proposed
      ↓
decide             final status
      ↓
Confirmed, sufficiently-confident diagnosis
      ↓
written to service-scoped memory (write-policy gated) — surfaced as
advisory context on this service's next investigation
```

If the process crashes anywhere after `evaluate_policy` has recorded a human's decision but before its own checkpoint is written, resuming re-enters that node from the top (LangGraph's documented contract) — the idempotency guard is what stops that from executing the remediation a second time. See [Architecture](#architecture) and the [retrospective](#project-retrospective--engineering-decisions) for the full mechanism.

## Architecture

| Component | Location | Role |
|---|---|---|
| API | `src/opspilot/main.py` | FastAPI app — auth, CORS, error handling, `/health` |
| Synthetic environment | `src/opspilot/seed/`, `models.py` | Deterministic scenario generator + ORM: services, scenarios, deployments, metrics, logs, dependency status, runbooks |
| Agent graph | `src/opspilot/agent/graph.py`, `agent/nodes.py` | The LangGraph state machine diagrammed above |
| Tools | `src/opspilot/agent/tools.py` | Five read-only tools, scoped per scenario — the model has no write tool, ever |
| Policy engine | `src/opspilot/agent/policy.py` | `evaluate() -> EXECUTE\|REQUIRE_APPROVAL\|BLOCK\|ESCALATE`, a pure function of action type. Unrecognized action types fail closed (`BLOCK`), not open |
| Remediation tools | `src/opspilot/agent/remediation_tools.py` | Simulated, schema-validated, parameterized actions — never free-text or shell |
| Human-in-the-loop | `agent/nodes.py::evaluate_policy`, `routers/incidents.py` | `interrupt()` pause + `POST /incidents/{id}/approvals` resume; SLA-timeout auto-escalation |
| Durable checkpointing | `src/opspilot/agent/checkpointer.py` | LangGraph's Postgres checkpointer — survives a real process restart, not just a request boundary |
| Idempotency | `src/opspilot/agent/idempotency.py` | Deterministic key + immediately-committed claim, so a node re-entered after a crash never re-executes a remediation |
| Process identity | `src/opspilot/process_identity.py` | Per-process random id stamped on approval audit rows — makes a crash-and-resume directly observable in the timeline |
| Memory | `src/opspilot/memory.py` | Service-scoped write policy, consolidation, retrieval, prompt formatting |
| Evaluation harness | `src/opspilot/eval/` | Deterministic + LLM-as-judge scoring against the golden dataset; regression comparison between two saved runs |
| Tracing | `src/opspilot/agent/tracing.py` | Langfuse — one trace per investigation, a span per node/model-call/tool-call, key-based redaction |
| Incident API | `src/opspilot/routers/incidents.py` | Create / run / approve / list / timeline — the real production entrypoint the dashboard and this README's curl walkthrough both use |
| Persistence | `alembic/`, PostgreSQL 16, SQLAlchemy 2.0 | Every schema change is a reviewed migration; the checkpointer's own tables are deliberately outside Alembic's control (see retrospective) |
| Dashboard | `web/` (Next.js 16, App Router) | Incident lifecycle, durable-workflows view, per-service memory view, eval scorecards — every page live against the real backend |
| CI | `.github/workflows/ci.yml` | Lint, migrate, test, and a [gitleaks](https://github.com/gitleaks/gitleaks) secret scan on every push |

## AI / ML decisions vs. deterministic code

| AI decides | Deterministic code decides |
|---|---|
| Which tools to call, and in what order | Whether a proposed action is safe enough to execute — the policy engine, keyed only on action type |
| The root-cause diagnosis and which evidence it cites | Whether that cited evidence was actually gathered (the hallucination guard) |
| The recommended remediation action | Whether a confirmed diagnosis is worth writing to memory (a confidence floor, never the model's own say-so) |
| Its own stated confidence | Whether a remediation call has already executed once (idempotency) |
| — | Step/cost ceilings on one investigation run |
| — | Whether a paused approval has aged past its SLA and should auto-escalate |
| — | Whether a resumed process is the same one that paused, surfaced in the audit trail |

Model: `claude-sonnet-5` by default (`ANTHROPIC_MODEL`, swappable). Every model call is forced tool-use against a Pydantic schema (`submit_diagnosis`) — raw model output is never trusted as a decision, only validated as a proposal.

## Evaluation

`opspilot.eval` runs every scenario in the 17-scenario golden dataset through the exact same `investigate()` entrypoint the real Incident API uses — no eval-mode bypass of the policy engine, so a scenario whose ground truth expects `BLOCK` is only ever actually `BLOCK`ed here because the same graph and policy module production traffic depends on produced that verdict.

- **Deterministic** (`eval/deterministic.py`, no model call): policy-verdict match, recommended-action exact match, tool-selection score, unnecessary/errored tool calls, steps, cost, tokens, latency.
- **LLM-as-judge** (`eval/judge.py`, one structured-output call per scenario): diagnosis accuracy, evidence groundedness (a RAGAS-style faithfulness rubric, borrowed as an idea rather than imported as a dependency — RAGAS targets RAG pipelines, not tool-selection/policy-compliance scoring), remediation quality, escalation correctness. Four independent scores, never blended into one number.
- **Regression** (`eval/regression.py`): compares two saved runs and flags any metric that moved past a threshold — proven reproducibly with synthetic before/after runs (`tests/test_eval_regression.py`) rather than relying on live model variance between two paid runs.
- **Memory's measured impact** (`tests/test_memory_eval_impact.py`): the same harness, unchanged, run twice against a golden scenario primed with its own confirmed diagnosis as memory — once with retrieval on, once off. `mean_steps_used` drops from 5 to 2 with memory enabled; `recommended_action_exact_match_rate` and `policy_verdict_accuracy` stay at 1.0 either way. Memory changes efficiency, not correctness, and the comparison is exact and reproducible in CI at zero live cost.

```bash
python -m opspilot.eval --scenarios checkout-deploy-outage,payments-db-latency --label baseline
python -m opspilot.eval --scenarios checkout-deploy-outage,payments-db-latency --label candidate --compare-to baseline
python -m opspilot.eval --no-judge     # deterministic checks only, still real agent calls
python -m opspilot.eval --no-memory    # disable memory retrieval for this run
```

Prints a scorecard, saves the full result to `eval_runs/<label>.json` (gitignored — regenerable output, not a source artifact, the same reasoning as not committing a `node_modules/` or build folder), and — with `--compare-to` — a regression report. Results are also browsable at `/eval` and `/eval/[label]` on the dashboard once you've run at least one.

**A fresh clone's `/eval` page is empty by design** — no eval run has ever been committed to this repo, on purpose, since a committed result would freeze one moment of live model behavior as if it were permanent project state. This has been verified for real, not just written into the harness and left untested: a 3-scenario run (`--no-judge`, `claude-haiku-4-5`) was run against the actual running dashboard, cost **$0.1044**, and was confirmed visible end to end at `/eval` and `/eval/[label]` before being deleted again — the same one-time-verify-then-discard discipline every other eval run in this project's history has followed. Run the command above yourself to see it populated.

## Observability

Every investigation gets one Langfuse trace (`opspilot.agent.tracing`), with a nested span per graph node, a `generation` observation per model call (messages, output, token usage, cost), and a `tool` observation per tool invocation — all tagged with `graph_version`/`prompt_version` metadata, so a scorecard change is traceable to the exact code that produced it. Tracing is observability, never control: the Langfuse client no-ops gracefully whenever credentials aren't set, so an unconfigured account changes visibility, never behavior. An explicit key-based redaction pass strips anything secret-shaped from every payload before it leaves the process.

Every `Incident` persists the trace id of its most recent run/resume, exposed as a "View trace" link on the incident detail page and on each eval-run scenario row — the drill-through from a score or a status back to the exact reasoning and tool calls that produced it.

## Security

- **Authentication** — `X-API-Key` header, constant-time comparison, required on every route except `/health`.
- **Deterministic policy gate** — the LLM's own stated confidence never influences whether an action executes; proven adversarially (`tests/test_policy.py`).
- **Read-only tools during investigation** — the model has no write/remediation tool available to it at any point; only deterministic code (`evaluate_policy`) ever calls a remediation function, and only after a verdict permits it.
- **Rate limiting & body-size limits** — per-API-key, in-memory (single-process — the right amount of complexity at this scale; see `auth.py`).
- **Input validation** — Pydantic constraints on every write path, including approval parameters, validated against the same schema the remediation tool itself uses, before the graph is ever resumed.
- **Idempotent execution** — a crash-and-resume can never execute a remediation twice; see [Architecture](#architecture).
- **No secrets in persisted state** — verified directly: GraphState (the only thing ever checkpointed) has no field shaped like a secret, checked against a real persisted checkpoint row using the same redaction key list Langfuse traces use.
- **Environment-based secrets only** — nothing reads a credential from a committed file. `.env`/`.env.local` are gitignored and were never part of this repository's git history (verified with `git log --all --full-history`).
- **`.dockerignore`** — defensive hardening so a future Dockerfile change can't accidentally bake `.env`/`.git`/local caches into a built image (the current Dockerfile already avoids this by only ever `COPY`ing explicit paths).
- **No secrets in git** — verified with `gitleaks` across the full commit history before this repository was made public, and re-run on every push in CI.
- **The dashboard reads its API key server-side only** (`OPSPILOT_API_KEY`, deliberately not `NEXT_PUBLIC_`-prefixed) — it never reaches the browser bundle. See `web/lib/api.ts`.
- **Append-only audit trail** — every tool call, approval request, and approval decision is its own row, in order, with the approving actor's identity recorded. Nothing here is ever updated or deleted.

If you ever find a real secret in this repo, treat it as already compromised: rotate it immediately, then remove it.

## Limitations

Presented as deliberate scope, not an unfinished roadmap:

- **Runs via Docker Compose, not deployed to a live URL.** This project's subject is the agent/policy/durability layer, not infrastructure hosting — Compose is the supported and sufficient way to run, test, and demo the whole system end to end. See [Setup](#setup--local-development).
- **Single-operator scope, scoped by service, not multi-tenant.** Memory and authorization are keyed by *service* (checkout-api, payments-api, …) — this system investigates a fixed set of services for one operator, not multiple customer organizations, so there's no tenant boundary in the design.
- **Keyword/exact-match memory retrieval, not embeddings.** Precise enough at this corpus size (a handful of memory rows per service); pgvector is the right tool at a larger scale, not at this one.
- **One model for every step.** Evidence-gathering and diagnosis share the same model rather than being cost-tiered across steps — the simplest design that satisfies what this project set out to demonstrate.
- **No queue, no Redis, no Celery, at any version.** LangGraph's own Postgres checkpointer covers durability at this scale — there is no multi-worker fan-out or backpressure problem anywhere in this system that a queue would solve.
- **Simulated remediation only, by design.** `rollback_deployment`, `restart_service`, `scale_service`, `toggle_feature_flag` all act against the synthetic environment, never a real external system — this project's subject is the decision/durability layer around remediation, not a specific ops-tooling integration.
- **Rate limiting is in-memory, single-process.** Correct for a single-instance deployment; a shared store would be the change behind multiple workers.
- **No "verifying recovery" async-wait node.** The design blueprint mentions this in passing for a later phase; there's no post-remediation recovery-verification step anywhere in this codebase to make durable in the first place, so it was never built rather than invented from a four-word spec. See the retrospective for the full reasoning.

## Demo / Try it

A full guided walkthrough — including how to demonstrate the crash-and-resume/idempotency guarantee, which needs one manual terminal step (restarting the actual API process) rather than anything clickable in the UI — lives in **[`docs/demo-script.md`](docs/demo-script.md)**.

Six screenshots below, all from one real, live run of the actual application (no mock data, no staged UI) — a `checkout-deploy-outage` investigation that paused for approval, survived a real `docker compose restart api` mid-pause, was approved, and wrote a memory entry, plus a `checkout-cpu-overload-traffic-spike` investigation and one real evaluation run.

**Evidence-grounded diagnosis** — the specific metrics, logs, deployment, and dependency evidence the model actually cited, not just a conclusion:

![Incident diagnosis](docs/screenshots/incident-diagnosis.png)

**Human approval, durably queued** — a state-changing action pauses for a human; the durable-workflows view reads this back entirely from Postgres, with a live elapsed-wait timer:

![Human approval and durable workflow](docs/screenshots/human-approval.png)

**Crash recovery** — the API process was actually killed and restarted while this incident sat paused; the timeline says so explicitly rather than the resume looking identical either way:

![Crash recovery timeline entry](docs/screenshots/crash-recovery.png)

**A real evaluation run** — deterministic scoring against ground truth, including a genuine model/ground-truth disagreement on one adversarial scenario, left as-is rather than tuned away:

![Evaluation run scorecard](docs/screenshots/evaluation-dashboard.png)

**Service memory** — what OpsPilot learned from the incident above, linked back to the incident that produced it:

![Service memory entry](docs/screenshots/service-memory.png)

**Evidence over assumption** — a genuine traffic spike, confirmed only after checking deployments and dependency health too, not just reacting to the symptom:

![Evidence-based investigation](docs/screenshots/evidence-based-investigation.png)

What's on the dashboard, briefly (every page below is live against the real backend, nothing is static or mocked):

| Page | Shows |
|---|---|
| `/` | Incident list, a working "Create & run" form (a real investigation, real Anthropic call), pending-approvals summary |
| `/incidents/[id]` | Graph-state indicator, live approval form, diagnosis, full ordered audit timeline, "View trace" link to Langfuse |
| `/workflows` | Incidents currently paused and waiting on a human, with live-computed elapsed wait time |
| `/services/[name]/memory` | What OpsPilot has confirmed about a service, with a working delete/correction action |
| `/eval`, `/eval/[label]` | Saved evaluation-harness runs and per-scenario scorecards |

## Setup / local development

```bash
cp .env.example .env   # edit API_KEY and ANTHROPIC_API_KEY
docker compose up --build
```

This runs migrations, seeds the 17 golden scenarios (idempotently — safe to restart), sets up the durable checkpoint tables, and starts the API on `http://localhost:8000`.

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

**Dashboard:**

```bash
cd web
cp .env.example .env.local   # OPSPILOT_API_KEY must match the backend's API_KEY
npm install
npm run dev                  # http://localhost:3000 — requires the backend running above
```

**Without Docker:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d db      # start Postgres only

cp .env.example .env
export $(grep -v '^#' .env | xargs)   # or use direnv / your own tooling

alembic upgrade head
python -m opspilot.seed
uvicorn opspilot.main:app --reload
```

**Optional tracing:** add `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (and `LANGFUSE_BASE_URL` if your account isn't on the default region) to `.env` to see real traces. Leaving them unset is fully supported — the agent runs identically, it's just invisible to Langfuse.

## Tests

Tests need a running Postgres reachable via `DATABASE_URL`, and `API_KEY` set in the environment (tests assert against whatever key is actually configured — no hardcoded test key baked into application code). No `ANTHROPIC_API_KEY` required — every test scripts a fake model client.

```bash
docker compose up -d db
export DATABASE_URL=postgresql+psycopg://opspilot:local-dev-only-not-a-secret@localhost:5432/opspilot
export API_KEY=test-key
export CORS_ORIGINS=http://localhost:3000
alembic upgrade head
pytest -q
```

`test_migrations.py` deliberately downgrades and re-upgrades the schema as part of the round-trip check — don't point it at a database you care about.

Checkpointing and remediation idempotency are durable across process restarts (that's the point) — two session-scoped fixtures in `conftest.py` truncate the checkpoint and `remediation_executions` tables once per test run, so a fixed `thread_id` some test used in a *previous* pytest invocation against the same Postgres container can't resume stale state instead of starting fresh.

## License

[MIT](LICENSE) — free to use, modify, and adapt.

## Project retrospective / engineering decisions

v0.5.0 freezes this project as a portfolio piece and as the baseline for a
future multi-agent build. Five versions, one rule threaded through all of
them: **the LLM proposes, deterministic code decides.** The model interprets
evidence, picks tools, forms a hypothesis, and recommends an action. It
never decides for itself that an action is safe enough to run — that
judgment is made once, in code, by a policy engine keyed only on
`action_type`, never on diagnosis text or the model's own stated
confidence. Nothing in five versions of scope growth ever bent that rule;
each version's own tests include an adversarial case proving it (a
maximally-confident, maximally-urgent diagnosis still gets `REQUIRE_APPROVAL`
if that's what the action type maps to — see `tests/test_policy.py`).

### The arc, in full technical detail

**v0.1 — Raw Tool-Using Agent.** An explicit while-loop against the model API. Five read-only tools (`get_metrics`, `get_logs`, `get_recent_deployments`, `get_dependency_status`, `get_runbook`), each scoped in code to one scenario — read-only by construction, not by convention: with no action to gate yet, there's nothing dangerous a misbehaving loop can do. A hard step/cost ceiling, enforced by code, from day one — nothing stops a misbehaving loop from calling tools in an expensive cycle unless something deterministic does. FastAPI + PostgreSQL (via Alembic) + Pydantic + Docker + GitHub Actions CI from the start — production foundations, not deferred. A 17-scenario golden dataset (`opspilot.seed.scenarios`) with a structured **ground-truth block** per scenario (injected cause, expected evidence, expected diagnosis, expected action, expected policy verdict) — reviewable by a human without ever running the agent, and covering every policy verdict at least once across three services: deployment-caused and non-deployment-caused incidents with the same surface symptom, dependency failures, resource exhaustion (CPU, memory, a stuck-process variant that looks like resource exhaustion but isn't), deliberately misleading correlations, incomplete/ambiguous evidence, an already-self-resolved blip, and one action (`delete_data`) added specifically so the policy engine's `BLOCK` verdict is reachable by a real diagnosis, not just a synthetic unit test.

**v0.2 — Reliable Agent.** The loop becomes a LangGraph state machine (`opspilot.agent.graph`): `gather_context` self-loops via a conditional edge until `submit_diagnosis` or a ceiling is hit, then `hypothesize` (deterministically checks the diagnosis's cited evidence was actually gathered — a hallucination guard, not a semantic check) and `classify_risk` (informational risk-tier preview) run once. The deterministic policy engine (`opspilot.agent.policy`) becomes the real gate between diagnosis and action — `evaluate(ProposedAction) -> EXECUTE|REQUIRE_APPROVAL|BLOCK|ESCALATE`, keyed *only* on `action_type`. An unrecognized action type fails closed, not open. Simulated remediation tools (`rollback_deployment`, `restart_service`, `scale_service`, `toggle_feature_flag`) are introduced here, schema-validated and parameterized, never a free-text or shell action, and deliberately **not** exposed to the model as callable tools — only `evaluate_policy` ever calls one, and only after a verdict permits it. Real human-in-the-loop: `evaluate_policy` calls LangGraph's `interrupt()` on `REQUIRE_APPROVAL`, pausing the graph mid-node. A process-lifetime `InMemorySaver` checkpointer makes that pause resumable from a different HTTP request — real pause/resume within one running process, but a restart loses it. **That gap is named explicitly at the time, not discovered later**: "durable, restart-surviving checkpointing is v0.5's job, not v0.2's" is a comment written into this version's own code, before v0.3 or v0.4 existed. A paused incident nobody decides on in time auto-escalates via a lazy SLA-timeout check (no background scheduler — checked whenever anything next reads the incident). Reliability: a model call that exhausts its bounded retries ends the investigation in a defined `incomplete_provider_error` state, a graceful give-up, not a crash. An append-only audit trail: every tool call, approval request, and approval decision becomes its own row, in order, with the approving actor's identity recorded — nothing here is ever updated or deleted. A first dashboard: incident list, pending-approvals queue, a graph-state row showing which node an incident is conceptually sitting in, a live approval form, the full timeline.

**v0.3 — Measurable Agent.** The evaluation harness (`opspilot.eval`) runs every scenario through the exact same `investigate()` entrypoint the real Incident API uses — no eval-mode bypass of the policy engine, so a `BLOCK`-expected scenario is only ever actually `BLOCK`ed because the same graph, same policy module, and same interrupt mechanism production depends on produced that verdict. Deterministic scoring (policy/action match, tool selection, errors, cost, tokens, latency) plus LLM-as-judge scoring (diagnosis accuracy, evidence groundedness — the RAGAS-style faithfulness idea, borrowed as a rubric rather than imported as a dependency, since RAGAS targets RAG pipelines and this problem is tool-selection/policy-compliance scoring instead — remediation quality, escalation correctness: four independent scores, never blended into one). Regression comparison between two saved runs, proven reproducibly with synthetic before/after runs rather than live model variance between two paid runs. Full Langfuse tracing (`opspilot.agent.tracing`): one trace per investigation, a nested span per node, a `generation` observation per model call with token usage and cost, a `tool` observation per tool call, all tagged with `graph_version`/`prompt_version` metadata — tracing is observability, never control, so an unconfigured Langfuse account changes visibility, never behavior. An explicit key-based redaction pass strips anything secret-shaped before any payload leaves the process. An agent-facing eval dashboard (`/eval`, `/eval/[label]`) reads the harness's saved JSON runs directly (never a DB table) and drill-throughs straight into the corresponding Langfuse trace.

**v0.4 — Context-Aware Agent.** Service-scoped memory of confirmed diagnoses (`opspilot.memory`, `service_memory` table): a write-policy gate writes a row only for a confirmed diagnosis — never for `recommended_action == "escalate"` (the model's own "I'm not confident enough" signal), never below a configurable confidence floor — and only from the structured `SubmitDiagnosisArgs`/`InvestigationResult` objects, never raw model chatter. `outcome` is derived from the policy verdict and any approval decision, never from what the model claims happened. A same-service consolidation pass merges exact-duplicate patterns into one row with a growing `occurrence_count`. Every read is scoped by `service_id` — there is no function anywhere in the module capable of returning another service's rows, proven adversarially. Retrieval is wired into the real investigation, once per run (not once per `gather_context` iteration): the top few memory rows are rendered as a clearly-labeled "Prior related incidents" block appended to the system prompt, with an explicit contamination guardrail in the prompt text itself — memory may suggest a hypothesis, but current evidence always wins on contradiction. Its impact is *measured*, not assumed: a repeat-pattern eval scenario, run through the unchanged v0.3 harness with memory on vs. off, shows `mean_steps_used` drop from 5 to 2 while diagnosis/policy accuracy hold at 1.0 either way — reproducible, at zero live cost. A per-service memory dashboard (`/services/[name]/memory`) with a working delete/correction action for a wrong entry.

**v0.5 — Durable Agent.** The in-memory checkpointer named as a gap back in v0.2 becomes `PostgresSaver` (`opspilot.agent.checkpointer`) — every node transition persisted to the same Postgres database the rest of the app uses. The checkpointer's own tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) are deliberately outside this project's Alembic migrations: they're owned and versioned by `langgraph-checkpoint-postgres` itself via an idempotent `setup()` call, and hand-rolling migrations for a third-party library's private schema would couple this project to its internals for no benefit. Proven, not just wired in: a kill-and-resume harness kills the process at each of the graph's five node boundaries in turn — a statically-injected breakpoint plus an independently-constructed checkpointer standing in for "a genuinely different process attaching to the same durable store" — and asserts it always resumes from that exact boundary, never re-running `gather_context`'s one model call, through both the low-level graph API and the real production approval-resume HTTP path. Durability alone creates a subtler failure mode: LangGraph's documented contract is that resuming past an `interrupt()` call re-enters the entire node function from the top, so a crash between a human's decision being known and that node's own checkpoint being written means a later resume can reach the remediation call again for the identical decision. `execute_idempotently` (`opspilot.agent.idempotency`) closes that gap — a deterministic key over `(thread_id, action_type, target, params)`, claimed in its own immediately-committed session (deliberately not the long-lived per-investigation session, since the guarantee must hold even if that session's own eventual commit never runs). A second attempt at the same key returns the first attempt's recorded result instead of calling the remediation tool again, flagged `deduplicated: true`, surfaced directly in the incident's timeline rather than silently absorbed. Applied uniformly to both `REQUIRE_APPROVAL` and auto-executed `EXECUTE` paths, since both are subject to the same re-entry contract. Verified at the node level by calling the extracted, interrupt-free `_resolve_verdict` twice with identical inputs — the precise, direct simulation of the actual re-entry mechanism, rather than a fragile attempt to trigger a real crash mid-function. The final phase makes both properties *demonstrable*, not just true: `PROCESS_INSTANCE_ID` (`opspilot.process_identity`), a random id generated once per process lifetime, is stamped onto the "approval requested" and "approval decided" audit rows — two different values on the same incident is concrete, queryable evidence a restart happened in between, surfaced directly in that incident's own timeline ("… — resumed after a process restart") rather than only narrated by a demo script. A durable-workflows dashboard view (`/workflows`) shows incidents currently paused and waiting on a human, with live-computed elapsed wait time, read back entirely from Postgres.

### Decisions that shaped it

In case the reasoning is useful for the next project:

- **No Redis/Celery/queue at any version.** LangGraph's own Postgres checkpointer covers durability at this scale; there is no multi-worker fan-out, no backpressure problem, and no pub/sub need anywhere in this plan. A queue here would be technology added for the résumé, which the project's own design brief explicitly warned against.
- **pgvector deferred, not adopted speculatively.** At v0.4's actual corpus size — a handful of memory rows per service — keyword/exact-match lookup is faster to build, easier to evaluate, and just as correct. Add embeddings only once eval data shows retrieval actually under-performing at a larger corpus size, not in advance.
- **Memory and authorization scoped by *service*, not tenant.** OpsPilot is a single-operator tool investigating a fixed set of synthetic services — there's no second customer org to isolate from. Building multi-tenancy speculatively would be scope for its own sake.
- **v0.1 is read-only by construction, not by convention.** Remediation tools were deliberately held back until v0.2, once the policy engine existed — at v0.1 there is no action to gate yet, so there is nothing dangerous the agent can do even if the raw loop misbehaves. A real security property, not a limitation.
- **A hard per-incident cost/step ceiling from v0.1 onward.** Nothing else stops a misbehaving agent loop from calling tools in an expensive cycle — this is a deterministic guardrail, not an AI judgment call.
- **The "verifying recovery" async-wait node was deliberately not built.** The design blueprint mentions it in passing for v0.4/v0.5; there's no spec beyond a few words, it's not covered by any phase's own done-when criterion, and there's no post-remediation recovery-verification step anywhere in this codebase to make durable in the first place — inventing one from a passing mention would be speculative scope, not implementation.
- **Optional model tiering (cheap model for evidence-gathering, strong model for diagnosis) was flagged optional and never built.** Real, but not required for done — the single-model design stayed simpler for it.
- **RAGAS was never adopted, even though its ideas were.** RAGAS targets RAG pipelines (context precision/recall, faithfulness of an answer to retrieved chunks); OpsPilot's retrieval surface (runbooks, later memory rows) is too small and too structured to need a retrieval-eval framework. Its faithfulness/groundedness concept was borrowed as one dimension of a custom LLM-judge rubric instead.

### What a future multi-agent project inherits from this baseline

A synthetic environment and scenario contract with real ground truth; an eval harness that scores tool selection, policy compliance, and diagnosis quality independent of any one agent's implementation; a policy-engine pattern (deterministic gate, keyed on action type, fails closed on anything unrecognized) that generalizes to more than one agent proposing actions; durable, idempotent execution that doesn't care how many agents are proposing work, only that each proposed action executes at most once; and full tracing wired through every model and tool call, ready to carry a per-agent dimension when there's more than one agent to distinguish.
