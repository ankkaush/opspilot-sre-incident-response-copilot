import Link from "next/link";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function EvalRunsPage() {
  const runs = await api.listEvalRuns();

  return (
    <main>
      <p>
        <Link href="/">&larr; Incidents</Link>
      </p>
      <h1>Eval runs</h1>
      <p className="muted">
        Golden-dataset scorecards from <code>python -m opspilot.eval</code>. Each run scores the
        agent against ground truth — deterministic checks plus, where enabled, LLM-as-judge
        rubrics.
      </p>

      {runs.length === 0 ? (
        <p className="muted">
          No eval runs yet — run <code>python -m opspilot.eval</code> from the repo root.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Started</th>
              <th>Scenarios</th>
              <th>Completion</th>
              <th>Policy accuracy</th>
              <th>Diagnosis (judge)</th>
              <th>Cost</th>
              <th>Latency</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_label}>
                <td>
                  <Link href={`/eval/${encodeURIComponent(run.run_label)}`}>{run.run_label}</Link>
                </td>
                <td className="muted">{new Date(run.started_at).toLocaleString()}</td>
                <td>{run.scenario_count}</td>
                <td>{(run.completion_rate * 100).toFixed(0)}%</td>
                <td>{(run.policy_verdict_accuracy * 100).toFixed(0)}%</td>
                <td>{run.mean_diagnosis_accuracy != null ? run.mean_diagnosis_accuracy.toFixed(2) : "—"}</td>
                <td>${run.total_cost_usd.toFixed(4)}</td>
                <td>{run.mean_latency_seconds.toFixed(1)}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
