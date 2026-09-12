"""Compares two EvalRuns and flags metrics that moved by more than a
threshold — the "done when" check for this phase: a prompt/graph change
that degrades behavior should show up here as a regression, not require
someone to eyeball two scorecards side by side.
"""

from pydantic import BaseModel

from opspilot.eval.runner import EvalRun

# Every metric compared, in "higher is better" form. total_unnecessary_tool_calls
# and total_argument_errors are inverted at compare time since lower is better
# for those two.
_HIGHER_IS_BETTER = [
    "completion_rate",
    "policy_verdict_accuracy",
    "recommended_action_exact_match_rate",
    "mean_tool_selection_score",
    "mean_diagnosis_accuracy",
    "mean_evidence_groundedness",
    "mean_remediation_quality",
    "escalation_correctness_rate",
]


class MetricDelta(BaseModel):
    metric: str
    baseline: float
    candidate: float
    delta: float  # candidate - baseline, always in "higher is better" terms


class RegressionReport(BaseModel):
    baseline_label: str
    candidate_label: str
    regressions: list[MetricDelta]
    improvements: list[MetricDelta]
    unchanged: list[str]

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0


def compare_runs(baseline: EvalRun, candidate: EvalRun, *, threshold: float = 0.05) -> RegressionReport:
    regressions: list[MetricDelta] = []
    improvements: list[MetricDelta] = []
    unchanged: list[str] = []

    for metric in _HIGHER_IS_BETTER:
        b = getattr(baseline.aggregate, metric)
        c = getattr(candidate.aggregate, metric)
        if b is None or c is None:
            continue
        delta = round(c - b, 4)
        entry = MetricDelta(metric=metric, baseline=b, candidate=c, delta=delta)
        if delta <= -threshold:
            regressions.append(entry)
        elif delta >= threshold:
            improvements.append(entry)
        else:
            unchanged.append(metric)

    return RegressionReport(
        baseline_label=baseline.run_label,
        candidate_label=candidate.run_label,
        regressions=regressions,
        improvements=improvements,
        unchanged=unchanged,
    )


def format_regression_report(report: RegressionReport) -> str:
    lines = [f"=== Regression: {report.baseline_label} -> {report.candidate_label} ==="]
    if report.regressions:
        lines.append("REGRESSIONS:")
        for m in report.regressions:
            lines.append(f"  {m.metric}: {m.baseline:.3f} -> {m.candidate:.3f} ({m.delta:+.3f})")
    if report.improvements:
        lines.append("Improvements:")
        for m in report.improvements:
            lines.append(f"  {m.metric}: {m.baseline:.3f} -> {m.candidate:.3f} ({m.delta:+.3f})")
    if not report.regressions and not report.improvements:
        lines.append("No metric moved by more than the threshold.")
    return "\n".join(lines)
