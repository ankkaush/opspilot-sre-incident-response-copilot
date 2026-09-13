import Link from "next/link";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

function formatElapsed(since: string): string {
  const ms = Date.now() - new Date(since).getTime();
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export default async function WorkflowsPage() {
  const incidents = await api.listIncidents();
  const paused = incidents.filter((i) => i.status === "awaiting_approval");

  return (
    <main>
      <p>
        <Link href="/">&larr; All incidents</Link>
      </p>
      <h1>Durable workflows</h1>
      <p className="muted">
        Checkpointing (v0.5 Phase 1) means a paused incident survives a process restart, not just a
        request boundary — nothing below is held in memory anywhere, it&apos;s all read back from
        Postgres. Idempotent remediation (Phase 2) means approving one exactly once, even after a
        crash-and-resume, only ever executes the underlying action once. When a resume genuinely
        happened in a different process than the one that paused it, that incident&apos;s own timeline
        says so explicitly — look for &quot;resumed after a process restart.&quot;
      </p>

      <h2>Currently paused, waiting on a human</h2>
      {paused.length === 0 ? (
        <p className="muted">Nothing waiting on a human right now.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Service</th>
              <th>Action requested</th>
              <th>Confidence</th>
              <th>Waiting since</th>
              <th>Elapsed</th>
            </tr>
          </thead>
          <tbody>
            {paused.map((incident) => (
              <tr key={incident.id}>
                <td>
                  <Link href={`/incidents/${incident.id}`}>#{incident.id}</Link>
                </td>
                <td>{incident.service_name}</td>
                <td>{incident.pending_approval?.action_type}</td>
                <td>{incident.pending_approval?.confidence}</td>
                <td className="muted">
                  {incident.awaiting_since ? new Date(incident.awaiting_since).toLocaleString() : "—"}
                </td>
                <td>{incident.awaiting_since ? formatElapsed(incident.awaiting_since) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
