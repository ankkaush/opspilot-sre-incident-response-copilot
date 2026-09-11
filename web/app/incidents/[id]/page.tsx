import Link from "next/link";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

const KIND_LABELS: Record<string, string> = {
  incident_started: "Incident started",
  evidence_gathered: "Evidence gathered",
  diagnosis_formed: "Diagnosis formed",
  final_status: "Final status",
};

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: idParam } = await params;
  const id = Number(idParam);
  const [incident, timeline] = await Promise.all([api.getIncident(id), api.getTimeline(id)]);

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
