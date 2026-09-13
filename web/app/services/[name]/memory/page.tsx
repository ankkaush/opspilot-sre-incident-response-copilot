import Link from "next/link";
import { api } from "@/lib/api";
import { deleteMemoryEntry } from "@/lib/actions";

export const dynamic = "force-dynamic";

export default async function ServiceMemoryPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name: nameParam } = await params;
  const serviceName = decodeURIComponent(nameParam);
  const entries = await api.getServiceMemory(serviceName);

  return (
    <main>
      <p>
        <Link href="/">&larr; All incidents</Link>
      </p>
      <h1>{serviceName} — memory</h1>
      <p className="muted">
        What OpsPilot has confirmed about this service from past incidents. Advisory context
        surfaced to the model during investigation — never a substitute for current evidence.
      </p>

      {entries.length === 0 ? (
        <p className="muted">
          Nothing learned yet — memory is written when an incident closes with a confirmed,
          sufficiently-confident diagnosis.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symptom pattern</th>
              <th>Root cause</th>
              <th>Fix</th>
              <th>Outcome</th>
              <th>Confidence</th>
              <th>Seen</th>
              <th>Learned</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.symptom_pattern}</td>
                <td>{entry.root_cause}</td>
                <td>{entry.fix_applied}</td>
                <td>{entry.outcome}</td>
                <td>{entry.confidence.toFixed(2)}</td>
                <td>{entry.occurrence_count}x</td>
                <td className="muted">
                  {new Date(entry.created_at).toLocaleString()} ·{" "}
                  <Link href={`/incidents/${entry.source_incident_id}`}>
                    #{entry.source_incident_id}
                  </Link>
                </td>
                <td>
                  <form action={deleteMemoryEntry}>
                    <input type="hidden" name="memory_id" value={entry.id} />
                    <input type="hidden" name="service_name" value={serviceName} />
                    <button type="submit" className="deny">
                      Delete
                    </button>
                  </form>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
