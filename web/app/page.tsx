import Link from "next/link";
import { api } from "@/lib/api";
import { createAndRunIncident } from "@/lib/actions";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const [incidents, scenarios, services] = await Promise.all([
    api.listIncidents(),
    api.listScenarios(),
    api.listServices(),
  ]);
  const pending = incidents.filter((i) => i.status === "awaiting_approval");

  return (
    <main>
      <h1>OpsPilot</h1>
      <p className="muted">
        SRE Incident Response Copilot — synthetic environment · <Link href="/eval">Eval runs</Link>
        {" · "}
        <Link href="/workflows">Durable workflows</Link>
        {" · Memory: "}
        {services.map((service, i) => (
          <span key={service.id}>
            {i > 0 && ", "}
            <Link href={`/services/${service.name}/memory`}>{service.name}</Link>
          </span>
        ))}
      </p>

      <h2>Start a new investigation</h2>
      <form className="new-incident" action={createAndRunIncident}>
        <select name="scenario_key" required defaultValue="">
          <option value="" disabled>
            Choose a scenario…
          </option>
          {scenarios.map((s) => (
            <option key={s.key} value={s.key}>
              {s.title}
            </option>
          ))}
        </select>
        <button type="submit">Create &amp; run</button>
      </form>

      <h2>Pending approvals</h2>
      {pending.length === 0 ? (
        <p className="muted">Nothing waiting on a human right now.</p>
      ) : (
        <ul className="pending-queue">
          {pending.map((incident) => (
            <li key={incident.id}>
              <Link href={`/incidents/${incident.id}`}>#{incident.id}</Link> — {incident.service_name}:{" "}
              <strong>{incident.pending_approval?.action_type}</strong>
              {incident.awaiting_since && (
                <span className="muted"> · waiting since {new Date(incident.awaiting_since).toLocaleString()}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2>Incidents</h2>
      {incidents.length === 0 ? (
        <p className="muted">No incidents yet — start one above.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Service</th>
              <th>Scenario</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((incident) => (
              <tr key={incident.id}>
                <td>
                  <Link href={`/incidents/${incident.id}`}>#{incident.id}</Link>
                </td>
                <td>{incident.service_name}</td>
                <td>{incident.scenario_key}</td>
                <td>
                  <span className={`status status-${incident.status}`}>{incident.status}</span>
                </td>
                <td className="muted">{new Date(incident.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
