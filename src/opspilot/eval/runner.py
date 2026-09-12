"""Runs the golden dataset through the real investigation pipeline and
produces a scorecard.

Every scenario runs through `opspilot.agent.loop.investigate` — the exact
entrypoint the real Incident API uses. There is no eval-mode flag, no
separate code path, nothing that bypasses the policy engine: a scenario
whose ground truth expects BLOCK is only ever actually BLOCKed here because
the same graph, same policy module, and same interrupt mechanism the
production API depends on produced that verdict. That's the blueprint's
own security requirement for this phase, satisfied by construction rather
than by a separate check.
"""

import datetime as dt
import time

from pydantic import BaseModel
from sqlalchemy.orm import Session

from opspilot.agent.client import ChatFn
from opspilot.agent.loop import investigate
from opspilot.eval.deterministic import DeterministicScores, score_deterministic
from opspilot.eval.judge import JudgeError, JudgeScores, judge_investigation
from opspilot.models import Scenario
from opspilot.seed.scenarios import ALL_SCENARIOS, ScenarioSpec


class ScenarioEvalResult(BaseModel):
    scenario_key: str
    status: str
    deterministic: DeterministicScores
    judge: JudgeScores | None = None
    judge_error: str | None = None
    # v0.3 Phase 3 — the Langfuse trace for this specific scenario's run,
    # linked by id: exactly what "any eval run's score can be traced back
    # to the exact prompt version, tool calls, and reasoning" means in
    # practice, satisfied by carrying the same id investigate() already
    # returns straight through to here.
    langfuse_trace_id: str | None = None


class AggregateScores(BaseModel):
    scenario_count: int
    completion_rate: float

    policy_verdict_accuracy: float
    recommended_action_exact_match_rate: float
    mean_tool_selection_score: float
    total_unnecessary_tool_calls: int
    total_argument_errors: int

    mean_diagnosis_accuracy: float | None = None
    mean_evidence_groundedness: float | None = None
    mean_remediation_quality: float | None = None
    escalation_correctness_rate: float | None = None

    mean_steps_used: float
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    mean_latency_seconds: float


class EvalRun(BaseModel):
    run_label: str
    started_at: dt.datetime
    scenario_keys: list[str]
    scenarios: list[ScenarioEvalResult]
    aggregate: AggregateScores


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def _aggregate(results: list[ScenarioEvalResult]) -> AggregateScores:
    n = len(results)
    reached = [r for r in results if r.deterministic.reached_diagnosis]
    judged = [r for r in results if r.judge is not None]

    return AggregateScores(
        scenario_count=n,
        completion_rate=round(len(reached) / n, 4) if n else 0.0,
        policy_verdict_accuracy=_mean([r.deterministic.policy_verdict_correct for r in reached]) or 0.0,
        recommended_action_exact_match_rate=(
            _mean([r.deterministic.recommended_action_exact_match for r in reached]) or 0.0
        ),
        mean_tool_selection_score=_mean([r.deterministic.tool_selection_score for r in results]) or 0.0,
        total_unnecessary_tool_calls=sum(r.deterministic.unnecessary_tool_call_count for r in results),
        total_argument_errors=sum(r.deterministic.argument_error_count for r in results),
        mean_diagnosis_accuracy=_mean([r.judge.diagnosis_accuracy for r in judged]),
        mean_evidence_groundedness=_mean([r.judge.evidence_groundedness for r in judged]),
        mean_remediation_quality=_mean([r.judge.remediation_quality for r in judged]),
        escalation_correctness_rate=_mean(
            [1.0 if r.judge.escalation_correct else 0.0 for r in judged]
        ),
        mean_steps_used=_mean([float(r.deterministic.steps_used) for r in results]) or 0.0,
        total_cost_usd=round(sum(r.deterministic.estimated_cost_usd for r in results), 6),
        total_input_tokens=sum(r.deterministic.total_input_tokens for r in results),
        total_output_tokens=sum(r.deterministic.total_output_tokens for r in results),
        mean_latency_seconds=_mean([r.deterministic.latency_seconds for r in results]) or 0.0,
    )


def run_scenario(
    session: Session,
    scenario_row: Scenario,
    spec: ScenarioSpec,
    *,
    agent_chat_fn: ChatFn,
    judge_chat_fn: ChatFn | None,
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
) -> ScenarioEvalResult:
    started = time.monotonic()
    result = investigate(
        session,
        scenario_row,
        chat_fn=agent_chat_fn,
        thread_id=f"eval-{spec.key}-{started}",
        max_steps=max_steps,
        max_cost_usd=max_cost_usd,
    )
    latency = time.monotonic() - started

    deterministic = score_deterministic(spec, result, latency_seconds=latency)

    judge: JudgeScores | None = None
    judge_error: str | None = None
    if result.diagnosis is not None and judge_chat_fn is not None:
        try:
            judge = judge_investigation(judge_chat_fn, spec, result.diagnosis)
        except JudgeError as exc:
            judge_error = str(exc)

    return ScenarioEvalResult(
        scenario_key=spec.key,
        status=result.status,
        deterministic=deterministic,
        judge=judge,
        judge_error=judge_error,
        langfuse_trace_id=result.langfuse_trace_id,
    )


def run_eval(
    session: Session,
    *,
    agent_chat_fn: ChatFn,
    judge_chat_fn: ChatFn | None = None,
    scenario_keys: list[str] | None = None,
    run_label: str = "eval",
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
) -> EvalRun:
    specs = [s for s in ALL_SCENARIOS if scenario_keys is None or s.key in scenario_keys]
    if not specs:
        raise ValueError(f"No scenarios matched scenario_keys={scenario_keys!r}")

    results = []
    for spec in specs:
        scenario_row = session.query(Scenario).filter_by(key=spec.key).one()
        results.append(
            run_scenario(
                session,
                scenario_row,
                spec,
                agent_chat_fn=agent_chat_fn,
                judge_chat_fn=judge_chat_fn,
                max_steps=max_steps,
                max_cost_usd=max_cost_usd,
            )
        )

    return EvalRun(
        run_label=run_label,
        started_at=dt.datetime.now(dt.UTC),
        scenario_keys=[s.key for s in specs],
        scenarios=results,
        aggregate=_aggregate(results),
    )
