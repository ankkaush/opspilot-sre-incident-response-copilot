"""The "done when" for v0.3 Phase 2, made concrete and reproducible without
a live model: two synthetic EvalRuns, one deliberately worse than the
other, must show up as a measurable regression — not require a human to
eyeball two scorecards."""

import datetime as dt

from opspilot.eval.regression import compare_runs
from opspilot.eval.runner import AggregateScores, EvalRun


def _run(label: str, **overrides) -> EvalRun:
    defaults = dict(
        completion_rate=1.0,
        policy_verdict_accuracy=1.0,
        recommended_action_exact_match_rate=1.0,
        mean_tool_selection_score=1.0,
        total_unnecessary_tool_calls=0,
        total_argument_errors=0,
        mean_diagnosis_accuracy=0.9,
        mean_evidence_groundedness=0.9,
        mean_remediation_quality=0.9,
        escalation_correctness_rate=1.0,
        mean_steps_used=3.0,
        total_cost_usd=0.01,
        total_input_tokens=1000,
        total_output_tokens=200,
        mean_latency_seconds=2.0,
        scenario_count=1,
    )
    defaults.update(overrides)
    return EvalRun(
        run_label=label,
        started_at=dt.datetime.now(dt.UTC),
        scenario_keys=["checkout-deploy-outage"],
        scenarios=[],
        aggregate=AggregateScores(**defaults),
    )


def test_a_degraded_prompt_shows_up_as_a_regression():
    baseline = _run("baseline")
    degraded = _run(
        "degraded-prompt",
        policy_verdict_accuracy=0.6,  # dropped from 1.0
        mean_diagnosis_accuracy=0.5,  # dropped from 0.9
    )

    report = compare_runs(baseline, degraded, threshold=0.05)

    assert report.has_regressions
    regressed_metrics = {m.metric for m in report.regressions}
    assert "policy_verdict_accuracy" in regressed_metrics
    assert "mean_diagnosis_accuracy" in regressed_metrics
    assert "completion_rate" not in regressed_metrics


def test_an_improved_prompt_shows_up_as_an_improvement_not_a_regression():
    baseline = _run("baseline", mean_diagnosis_accuracy=0.6)
    improved = _run("improved-prompt", mean_diagnosis_accuracy=0.95)

    report = compare_runs(baseline, improved, threshold=0.05)

    assert not report.has_regressions
    assert any(m.metric == "mean_diagnosis_accuracy" for m in report.improvements)


def test_identical_runs_produce_no_regressions_or_improvements():
    baseline = _run("baseline")
    same = _run("same-again")

    report = compare_runs(baseline, same, threshold=0.05)

    assert report.regressions == []
    assert report.improvements == []
    assert "policy_verdict_accuracy" in report.unchanged


def test_small_moves_under_threshold_are_not_flagged():
    baseline = _run("baseline", mean_diagnosis_accuracy=0.90)
    slightly_lower = _run("noisy-rerun", mean_diagnosis_accuracy=0.88)

    report = compare_runs(baseline, slightly_lower, threshold=0.05)

    assert report.regressions == []


def test_missing_judge_scores_are_skipped_not_treated_as_zero():
    baseline = _run("baseline", mean_diagnosis_accuracy=None, escalation_correctness_rate=None)
    candidate = _run("no-judge-run", mean_diagnosis_accuracy=None, escalation_correctness_rate=None)

    report = compare_runs(baseline, candidate)

    compared_metrics = (
        {m.metric for m in report.regressions}
        | {m.metric for m in report.improvements}
        | set(report.unchanged)
    )
    assert "mean_diagnosis_accuracy" not in compared_metrics
