"""Read-only API for eval harness results (v0.3 Phase 3) — powers the
agent dashboard. Eval runs are JSON files on disk (opspilot.eval.storage),
not a database table; this router lists and reads them, attaching a
Langfuse trace URL per scenario for the dashboard's drill-through links.
"""

from fastapi import APIRouter, Depends, HTTPException

from opspilot.agent.tracing import get_trace_url
from opspilot.auth import require_api_key
from opspilot.eval.storage import list_run_labels, load_run_by_label
from opspilot.schemas import EvalRunOut, EvalRunSummary, EvalScenarioResultOut

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/eval-runs", response_model=list[EvalRunSummary])
def list_eval_runs() -> list[EvalRunSummary]:
    summaries = []
    for label in list_run_labels():
        run = load_run_by_label(label)
        if run is None:
            continue
        summaries.append(
            EvalRunSummary(
                run_label=run.run_label,
                started_at=run.started_at,
                scenario_count=run.aggregate.scenario_count,
                completion_rate=run.aggregate.completion_rate,
                policy_verdict_accuracy=run.aggregate.policy_verdict_accuracy,
                mean_diagnosis_accuracy=run.aggregate.mean_diagnosis_accuracy,
                total_cost_usd=run.aggregate.total_cost_usd,
                mean_latency_seconds=run.aggregate.mean_latency_seconds,
            )
        )
    return summaries


@router.get("/eval-runs/{label}", response_model=EvalRunOut)
def get_eval_run(label: str) -> EvalRunOut:
    run = load_run_by_label(label)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No eval run with label '{label}'.")

    scenarios = [
        EvalScenarioResultOut(
            scenario_key=s.scenario_key,
            status=s.status,
            deterministic=s.deterministic,
            judge=s.judge,
            judge_error=s.judge_error,
            langfuse_trace_id=s.langfuse_trace_id,
            langfuse_trace_url=get_trace_url(s.langfuse_trace_id),
        )
        for s in run.scenarios
    ]
    return EvalRunOut(
        run_label=run.run_label,
        started_at=run.started_at,
        scenario_keys=run.scenario_keys,
        scenarios=scenarios,
        aggregate=run.aggregate,
    )
