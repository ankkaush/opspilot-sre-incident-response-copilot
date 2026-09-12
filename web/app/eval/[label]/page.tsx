import Link from "next/link";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

function pct(value: number | null): string {
  return value != null ? `${(value * 100).toFixed(0)}%` : "—";
}

function score(value: number | null): string {
  return value != null ? value.toFixed(2) : "—";
}

export default async function EvalRunDetailPage({
  params,
}: {
  params: Promise<{ label: string }>;
}) {
  const { label: labelParam } = await params;
  const label = decodeURIComponent(labelParam);
  const run = await api.getEvalRun(label);
  const a = run.aggregate;

  return (
    <main>
      <p>
        <Link href="/eval">&larr; All eval runs</Link>
      </p>
      <h1>Eval run: {run.run_label}</h1>
      <p className="muted">{new Date(run.started_at).toLocaleString()}</p>

      <h2>Scorecard</h2>
      <table className="scorecard">
        <tbody>
          <tr>
            <td>Completion rate</td>
            <td>{pct(a.completion_rate)}</td>
            <td>Policy verdict accuracy</td>
            <td>{pct(a.policy_verdict_accuracy)}</td>
          </tr>
          <tr>
            <td>Recommended-action exact match</td>
            <td>{pct(a.recommended_action_exact_match_rate)}</td>
            <td>Tool selection (mean)</td>
            <td>{score(a.mean_tool_selection_score)}</td>
          </tr>
          <tr>
            <td>Diagnosis accuracy (judge)</td>
            <td>{score(a.mean_diagnosis_accuracy)}</td>
            <td>Evidence groundedness (judge)</td>
            <td>{score(a.mean_evidence_groundedness)}</td>
          </tr>
          <tr>
            <td>Remediation quality (judge)</td>
            <td>{score(a.mean_remediation_quality)}</td>
            <td>Escalation correctness (judge)</td>
            <td>{pct(a.escalation_correctness_rate)}</td>
          </tr>
          <tr>
            <td>Unnecessary tool calls</td>
            <td>{a.total_unnecessary_tool_calls}</td>
            <td>Argument errors</td>
            <td>{a.total_argument_errors}</td>
          </tr>
          <tr>
            <td>Total cost</td>
            <td>${a.total_cost_usd.toFixed(4)}</td>
            <td>Total tokens (in/out)</td>
            <td>
              {a.total_input_tokens}/{a.total_output_tokens}
            </td>
          </tr>
          <tr>
            <td>Mean steps used</td>
            <td>{a.mean_steps_used.toFixed(1)}</td>
            <td>Mean latency</td>
            <td>{a.mean_latency_seconds.toFixed(2)}s</td>
          </tr>
        </tbody>
      </table>

      <h2>Per-scenario results</h2>
      <table>
        <thead>
          <tr>
            <th>Scenario</th>
            <th>Status</th>
            <th>Policy</th>
            <th>Action match</th>
            <th>Diagnosis (judge)</th>
            <th>Trace</th>
          </tr>
        </thead>
        <tbody>
          {run.scenarios.map((s) => (
            <tr key={s.scenario_key}>
              <td>{s.scenario_key}</td>
              <td>
                <span className={`status status-${s.status}`}>{s.status}</span>
              </td>
              <td className={s.deterministic.policy_verdict_correct ? "pass" : "fail"}>
                {s.deterministic.policy_verdict_correct == null
                  ? "—"
                  : s.deterministic.policy_verdict_correct
                    ? "OK"
                    : "FAIL"}
              </td>
              <td className={s.deterministic.recommended_action_exact_match ? "pass" : "fail"}>
                {s.deterministic.recommended_action_exact_match == null
                  ? "—"
                  : s.deterministic.recommended_action_exact_match
                    ? "OK"
                    : "FAIL"}
              </td>
              <td>{s.judge ? s.judge.diagnosis_accuracy.toFixed(2) : "—"}</td>
              <td>
                {s.langfuse_trace_url ? (
                  <a href={s.langfuse_trace_url} target="_blank" rel="noreferrer">
                    View trace
                  </a>
                ) : (
                  <span className="muted">not traced</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
