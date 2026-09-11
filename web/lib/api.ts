// Server-only. This module is imported only from Server Components and
// Server Actions (never from a "use client" file), so OPSPILOT_API_KEY is
// read on the server and never shipped to the browser bundle.

const BASE_URL = process.env.OPSPILOT_API_BASE_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = process.env.OPSPILOT_API_KEY;
  if (!apiKey) {
    throw new Error(
      "OPSPILOT_API_KEY is not set. Copy web/.env.example to web/.env.local and fill it in."
    );
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "X-API-Key": apiKey,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`OpsPilot API ${res.status} on ${path}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export type Scenario = {
  id: number;
  key: string;
  service_id: number;
  title: string;
  incident_started_at: string;
};

export type Diagnosis = {
  diagnosis: string;
  evidence: string[];
  confidence: number;
  recommended_action: string;
};

export type Incident = {
  id: number;
  scenario_key: string;
  service_name: string;
  status: string;
  created_at: string;
  steps_used: number | null;
  estimated_cost_usd: number | null;
  diagnosis: Diagnosis | null;
  started_at: string | null;
  completed_at: string | null;
};

export type TimelineEntry = {
  kind: "incident_started" | "evidence_gathered" | "diagnosis_formed" | "final_status";
  step: number | null;
  label: string;
  detail: Record<string, unknown> | null;
  timestamp: string | null;
};

export type Timeline = {
  incident_id: number;
  entries: TimelineEntry[];
};

export const api = {
  listScenarios: () => apiFetch<Scenario[]>("/api/v1/scenarios"),
  listIncidents: () => apiFetch<Incident[]>("/api/v1/incidents"),
  getIncident: (id: number) => apiFetch<Incident>(`/api/v1/incidents/${id}`),
  getTimeline: (id: number) => apiFetch<Timeline>(`/api/v1/incidents/${id}/timeline`),
  createIncident: (scenarioKey: string) =>
    apiFetch<Incident>("/api/v1/incidents", {
      method: "POST",
      body: JSON.stringify({ scenario_key: scenarioKey }),
    }),
  runIncident: (id: number) =>
    apiFetch<Incident>(`/api/v1/incidents/${id}/run`, { method: "POST" }),
};
