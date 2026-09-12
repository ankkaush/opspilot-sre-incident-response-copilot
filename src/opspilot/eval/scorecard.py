"""Renders an EvalRun as a human-readable scorecard — the "done when"
artifact for this phase: running the suite should produce something a
person can read and understand without digging into raw JSON.
"""

from opspilot.eval.runner import EvalRun


def format_scorecard(run: EvalRun) -> str:
    a = run.aggregate
    lines = [
        f"=== OpsPilot Eval: {run.run_label} ({a.scenario_count} scenarios) ===",
        f"Completion rate:                {a.completion_rate:.0%}",
        f"Policy verdict accuracy:        {a.policy_verdict_accuracy:.0%}",
        f"Recommended-action exact match: {a.recommended_action_exact_match_rate:.0%}",
        f"Tool selection score (mean):    {a.mean_tool_selection_score:.2f}",
        f"Unnecessary tool calls (total): {a.total_unnecessary_tool_calls}",
        f"Argument errors (total):        {a.total_argument_errors}",
    ]
    if a.mean_diagnosis_accuracy is not None:
        lines += [
            f"Diagnosis accuracy (judge):     {a.mean_diagnosis_accuracy:.2f}",
            f"Evidence groundedness (judge):  {a.mean_evidence_groundedness:.2f}",
            f"Remediation quality (judge):    {a.mean_remediation_quality:.2f}",
            f"Escalation correctness (judge): {a.escalation_correctness_rate:.0%}",
        ]
    else:
        lines.append("Judge scores:                   (none — run without --no-judge to enable)")
    lines += [
        f"Mean steps used:                {a.mean_steps_used:.1f}",
        f"Total cost (USD):               ${a.total_cost_usd:.4f}",
        f"Total tokens (in/out):          {a.total_input_tokens}/{a.total_output_tokens}",
        f"Mean latency (s):               {a.mean_latency_seconds:.2f}",
        "",
        "Per-scenario:",
    ]
    for r in run.scenarios:
        d = r.deterministic
        marker = "OK" if d.reached_diagnosis and d.policy_verdict_correct else "!!"
        judge_bit = f" judge_diag={r.judge.diagnosis_accuracy:.2f}" if r.judge else ""
        lines.append(f"  [{marker}] {r.scenario_key:45s} status={r.status:20s}{judge_bit}")
    return "\n".join(lines)


def print_scorecard(run: EvalRun) -> None:
    print(format_scorecard(run))
