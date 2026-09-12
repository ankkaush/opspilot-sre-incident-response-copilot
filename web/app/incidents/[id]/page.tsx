import Link from "next/link";
import { api, currentGraphNode, GRAPH_NODES } from "@/lib/api";
import { decideApproval } from "@/lib/actions";

export const dynamic = "force-dynamic";

const KIND_LABELS: Record<string, string> = {
  incident_started: "Incident started",
  evidence_gathered: "Evidence gathered",
  diagnosis_formed: "Diagnosis formed",
  approval_requested: "Approval requested",
  approval_decided: "Approval decided",
  final_status: "Final status",
};

const NODE_LABELS: Record<(typeof GRAPH_NODES)[number], string> = {
  gather_context: "gather_context",
  hypothesize: "hypothesize",
  classify_risk: "classify_risk",
  evaluate_policy: "evaluate_policy",
  decide: "decide",
};

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  const [incident, timeline] = await Promise.all([api.getIncident(id), api.getTimeline(id)]);
  const activeNode = currentGraphNode(incident.status);

  return (
    <main>
      <p>
        <Link href="/">&larr; All incidents</Link>
      </p>
      <h1>
        Incident #{incident.id} — {incident.service_name}
      </h1>
      <p className="muted">
        Scenario: {incident.scenario_key} ·{" "}
        <span className={`status status-${incident.status}`}>{incident.status}</span>
      </p>

      <h2>Graph state</h2>
      <ol className="graph-state">
        {GRAPH_NODES.map((node) => (
          <li key={node} className={node === activeNode ? "graph-node active" : "graph-node"}>
            {NODE_LABELS[node]}
          </li>
        ))}
      </ol>

      {incident.status === "awaiting_approval" && incident.pending_approval && (
        <>
          <h2>Approval requested</h2>
          <p>
            <strong>{incident.pending_approval.action_type}</strong> on{" "}
            <strong>{incident.pending_approval.service}</strong> — confidence{" "}
            {incident.pending_approval.confidence}
          </p>
          <p className="muted">{incident.pending_approval.diagnosis}</p>

          <form className="approval-form" action={decideApproval}>
            <input type="hidden" name="incident_id" value={incident.id} />
            <input type="hidden" name="action_type" value={incident.pending_approval.action_type} />

            {incident.pending_approval.action_type === "rollback_deployment" && (
              <label>
                Target version
                <input type="text" name="target_version" placeholder="e.g. v2.7" required />
              </label>
            )}
            {incident.pending_approval.action_type === "toggle_feature_flag" && (
              <>
                <label>
                  Flag name
                  <input type="text" name="flag_name" placeholder="e.g. new-checkout-flow" required />
                </label>
                <label className="checkbox-label">
                  <input type="checkbox" name="enabled" /> Enable flag
                </label>
              </>
            )}

            <label>
              Your name
              <input type="text" name="actor" placeholder="e.g. alice" required />
            </label>

            <div className="approval-actions">
              <button type="submit" name="decision" value="approve" className="approve">
                Approve
              </button>
              <button type="submit" name="decision" value="deny" className="deny">
                Deny
              </button>
            </div>
          </form>
        </>
      )}

      {incident.diagnosis && (
        <>
          <h2>Diagnosis</h2>
          <p>{incident.diagnosis.diagnosis}</p>
          <p>
            Recommended action: <strong>{incident.diagnosis.recommended_action}</strong> · Confidence:{" "}
            {incident.diagnosis.confidence}
          </p>
          <p className="muted">Evidence cited:</p>
          <ul className="evidence-list">
            {incident.diagnosis.evidence.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </>
      )}

      {incident.status === "incomplete_step_ceiling" && (
        <p>
          The investigation stopped after {incident.steps_used} steps without reaching a diagnosis —
          the step ceiling, not the model, decided that. See the timeline below.
        </p>
      )}
      {incident.status === "incomplete_cost_ceiling" && (
        <p>
          The investigation stopped after exceeding its cost ceiling (~${incident.estimated_cost_usd}
          ) without reaching a diagnosis. See the timeline below.
        </p>
      )}
      {incident.status === "incomplete_provider_error" && (
        <p>
          The model was unreachable and gave up gracefully after exhausting retries, without reaching
          a diagnosis. See the timeline below.
        </p>
      )}

      <h2>Timeline</h2>
      <ol className="timeline">
        {timeline.entries.map((entry, index) => (
          <li key={index}>
            <div className="timeline-kind">
              {KIND_LABELS[entry.kind] ?? entry.kind}
              {entry.timestamp ? ` — ${new Date(entry.timestamp).toLocaleString()}` : ""}
            </div>
            <p className="timeline-label">{entry.label}</p>
            {entry.detail && (
              <details>
                <summary>Details</summary>
                <pre>{JSON.stringify(entry.detail, null, 2)}</pre>
              </details>
            )}
          </li>
        ))}
      </ol>
    </main>
  );
}
