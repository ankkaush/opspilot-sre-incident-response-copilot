# Demo script

A practical walkthrough for showing OpsPilot to someone else — four short demos, each against the real running system (real Anthropic calls, real Postgres, real Langfuse traces where configured). Nothing here is scripted UI behavior or mock data; every screen you'll show is a live page reading from the real backend.

**Setup, once:**

```bash
cp .env.example .env   # fill in API_KEY and ANTHROPIC_API_KEY
docker compose up --build -d
cd web && cp .env.example .env.local && npm install && npm run dev
```

Dashboard: `http://localhost:3000`. API: `http://localhost:8000`.

**On cost:** every "Create & run" click makes a real, billed call to the Anthropic API. This project defaults to `claude-sonnet-5` (`ANTHROPIC_MODEL` in `.env`). If you're demoing repeatedly and want to keep costs low, set `ANTHROPIC_MODEL=claude-haiku-4-5` — this project's own development testing with that model put a single investigation at roughly **$0.01–0.04**, and a 3-scenario evaluation run at roughly **$0.10**. Sonnet will cost more per call. Nothing in Demos A–C below is required to run more than once.

---

## Demo A — Normal investigation

**Path:** Home (`/`) → "Create & run" → incident detail → diagnosis/timeline → Langfuse trace.

1. On the home page, choose a scenario from the dropdown whose ground truth doesn't require human approval — **`checkout-api had a brief error blip that already recovered`** (`checkout-transient-blip-no-action`) is a good pick; its correct diagnosis is "no action needed," which the policy engine auto-executes without pausing. Click **Create & run**.
2. You're redirected straight to the incident detail page once the investigation finishes — this is a synchronous call, so the page load *is* the wait.
3. Look at: the **graph-state row** (shows `decide` as the active node — the investigation ran to completion), the **diagnosis** section (the model's root-cause explanation, its cited evidence, its confidence, the recommended action), and the **timeline** (`incident started → evidence gathered ×N → diagnosis formed → final status`, each with a timestamp, each tool call's arguments/result expandable).
4. If `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are set, click **View trace** at the top of the page. In Langfuse, show: the nested span per graph node, the `generation` span with the actual token counts and cost, the tool-call spans with their real arguments and results, and the `graph_version`/`prompt_version` tags in the trace metadata.

**What this proves:** the full loop — real tool calls against the synthetic environment, a real model call producing a structured diagnosis, the deterministic `hypothesize` node checking the diagnosis's evidence was actually gathered (not hallucinated), all the way to a closed incident — with every step attributable in Langfuse down to the token.

**Real vs. simulated:** the Anthropic call and the resulting diagnosis are real. The metrics/logs/deployments the model reads are synthetic, deterministic fixtures (`opspilot.seed`) — that's what makes the ground truth checkable at all.

**Cost:** one real investigation, ~$0.01–0.04 with `claude-haiku-4-5`, more with the default `claude-sonnet-5`.

---

## Demo B — Human approval

**Path:** Create & run → AI recommendation → `REQUIRE_APPROVAL` → `/workflows` → approval → resume → remediation → timeline.

1. From the home page, run **`Checkout error rate spike following v2.8 deploy`** (`checkout-deploy-outage`). Its correct fix is a deployment rollback — a state-changing action, gated by the policy engine regardless of how confident the diagnosis is.
2. The incident detail page shows status **`awaiting_approval`** and an approval form (target version to roll back to, your name).
3. Open **`/workflows`** in another tab — this incident is listed under "Currently paused, waiting on a human," with a live-computed elapsed-wait time. This is the durable-workflow view: everything on it is read back from Postgres, nothing is held in server memory.
4. Back on the incident page, fill in a target version (e.g. `v2.7`) and your name, click **Approve**.
5. The page reloads with status **`diagnosed`**. Scroll to the timeline: `approval_requested → approval_decided (Approved by <you>) → final_status`. Expand the `approval_decided` entry's detail — the `result` object is the actual (simulated) remediation call's return value.

**What this proves:** the policy engine's core rule — the model recommending an action with high confidence is not sufficient for it to execute. Only a human clicking Approve, with concrete parameters validated against the same schema the remediation tool itself uses, moves it forward.

**Real vs. simulated:** the diagnosis and the approval gate are real (a genuinely different HTTP request resumes a genuinely paused LangGraph checkpoint). The rollback itself is simulated — it returns a structured "this would have run" result rather than calling a real deployment system; see the README's Limitations section for why.

**Cost:** one real investigation, same range as Demo A. Approving costs nothing further (no model call on resume unless the graph needs to re-evaluate — it doesn't, here).

---

## Demo C — Crash recovery + idempotency

**Path:** start an investigation that reaches approval → restart the API process/container from the terminal → return to the incident → approve → show "resumed after a process restart" → discuss why remediation only ever executes once.

This is the demo that's actually worth narrating carefully — it's the one most agent projects can't do at all.

1. Same as Demo B, steps 1–2: run `checkout-deploy-outage`, land on the paused incident. **Note the incident ID from the URL** (e.g. `/incidents/7`).
2. **Now kill and restart the actual server process.** This is a real terminal action, not a UI click — restarting the process from within its own UI would prove nothing:
   - **Docker Compose:** `docker compose restart api`
   - **Local, no Docker:** stop the running `uvicorn` process (Ctrl+C) and start it again: `uvicorn opspilot.main:app --reload`
3. Wait for it to come back: `curl http://localhost:8000/health`.
4. Go back to the same incident's page in the browser and refresh. **It still shows `awaiting_approval`, with the same pending approval details** — this alone is durability made visible: the pause was never held in the old process's memory, it was read back from Postgres by a brand-new process that has no idea a previous one ever ran.
5. Approve it, same as Demo B. The incident resolves to `diagnosed`.
6. Open the timeline, find the `approval_decided` entry. Its label now reads:
   **"Approved by \<you\> — resumed after a process restart."**
   This isn't narrated or faked — `PROCESS_INSTANCE_ID` (`opspilot.process_identity`) is a random id generated fresh at import time in *every* process. The process that requested the approval and the process that decided it are stamped with two different ids; the label only appears when they genuinely differ.

**What this proves, precisely — and what it doesn't:** steps 1–6 above directly, live, prove **durability**: a paused investigation and its eventual resolution survive an actual process restart, and that fact is queryable in the audit trail, not just claimed. They do **not**, by themselves, exercise the **idempotency** guard — a single Approve click only ever calls the remediation path once, restart or not, so there's nothing to accidentally double-execute in this exact sequence. The specific failure window idempotency protects against (LangGraph re-entering `evaluate_policy` from the top mid-resume, after the human's decision is known but before that node's own checkpoint is written) isn't something you can trigger safely through the UI or a curl retry — the API itself refuses a second `/approvals` call once an incident has left `awaiting_approval` (`409`), so that path is already closed at a layer above the graph. That narrower guarantee is proven where it actually needs to be proven — at the exact mechanism — by the automated test suite:

```bash
pytest -q tests/test_idempotency.py
```

This calls the extracted, interrupt-free half of `evaluate_policy` twice in a row with the identical decision — the precise, direct simulation of the real re-entry mechanism — and asserts the remediation tool is invoked exactly once, with the second call flagged `deduplicated: true` and both calls' results identical. Zero cost, runs in under a second, and is the same suite this project's CI runs on every push.

**Optional, for the curious — see the guard's own table:**

```bash
docker compose exec db psql -U opspilot -d opspilot -c \
  "SELECT action_type, target, created_at FROM remediation_executions ORDER BY created_at DESC LIMIT 5;"
```

One row for the action you just approved — the durable record `execute_idempotently` checks before ever calling a remediation tool a second time.

**Real vs. simulated:** the process restart is a real kill and restart of the actual server. The checkpoint recovery and the process-identity mismatch are real, observed facts, not staged. The remediation action itself is simulated (as in Demo B).

**Cost:** one real investigation, same range as Demo A/B. The restart and re-approval cost nothing further.

---

## Demo D — Evaluation

**Path:** run the evaluation harness → `/eval` → inspect scorecard → trace.

1. From the project root (not the dashboard):
   ```bash
   python -m opspilot.eval --scenarios checkout-deploy-outage,payments-db-latency,checkout-corrupted-data-tempting-wipe --no-judge --label demo-run
   ```
   `--no-judge` skips the LLM-as-judge call, keeping this to deterministic scoring only (cheaper, and the number a CI regression gate would actually watch). Drop `--no-judge` to also see diagnosis-accuracy/evidence-groundedness/remediation-quality scores from a judge model.
2. It prints a scorecard to the terminal and saves it to `eval_runs/demo-run.json`.
3. Open `/eval` in the dashboard — the run is listed with its aggregate metrics (completion rate, policy-verdict accuracy, cost, latency).
4. Click into it (`/eval/demo-run`) — the full scorecard, plus a per-scenario table (policy-verdict correctness, action exact-match, judge score if you ran with judging) and a **View trace** link per scenario straight into that scenario's Langfuse trace.

**What this proves:** every scenario runs through the exact same `investigate()` entrypoint the real Incident API uses — there's no separate, easier "eval mode" of the agent. A scenario whose ground truth expects `BLOCK` (`checkout-corrupted-data-tempting-wipe`) is only ever actually `BLOCK`ed here because the same policy engine production traffic depends on produced that verdict.

**Real vs. simulated:** the scoring is real (deterministic checks are pure code; the optional judge call is a real, separate Anthropic call). The scenarios themselves are the same synthetic fixtures every other demo uses.

**Cost:** three scenarios, `--no-judge` — roughly **$0.05–0.15** depending on model (this project's own 3-scenario smoke test with `claude-haiku-4-5` cost $0.10). Add judging for a modest additional per-scenario cost (one structured-output call each).

---

## After the demo

`eval_runs/demo-run.json` and anything else generated above are gitignored — safe to leave, safe to delete, regenerable at any time. No cleanup is required for the incidents or memory rows created during the demos; they're exactly what a real, honest record of what happened is supposed to look like.
