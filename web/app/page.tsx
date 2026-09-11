import Link from "next/link";
import { api } from "@/lib/api";
import { createAndRunIncident } from "@/lib/actions";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const [incidents, scenarios] = await Promise.all([api.listIncidents(), api.listScenarios()]);

  return (
    <main>
      <h1>OpsPilot</h1>
      <p className="muted">SRE Incident Response Copilot — synthetic environment</p>

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
