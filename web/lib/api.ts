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
  if (res.status === 204) {
    return undefined as T;
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

export type Service = {
  id: number;
  name: string;
  description: string;
};

export type ServiceMemoryEntry = {
  id: number;
  service_id: number;
  source_incident_id: number;
  symptom_pattern: string;
  root_cause: string;
  fix_applied: string;
  outcome: string;
  confidence: number;
  occurrence_count: number;
  created_at: string;
};

export type Diagnosis = {
  diagnosis: string;
  evidence: string[];
  confidence: number;
  recommended_action: string;
};

export type PendingApproval = {
  action_type: string;
  service: string;
  diagnosis: string;
  confidence: number;
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
  pending_approval: PendingApproval | null;
  awaiting_since: string | null;
  langfuse_trace_id: string | null;
  langfuse_trace_url: string | null;
};

export type TimelineEntry = {
  kind:
    | "incident_started"
    | "evidence_gathered"
    | "diagnosis_formed"
    | "approval_requested"
    | "approval_decided"
    | "final_status";
  step: number | null;
  label: string;
  detail: Record<string, unknown> | null;
  timestamp: string | null;
};

export type Timeline = {
  incident_id: number;
  entries: TimelineEntry[];
};

// The graph node an incident is conceptually "sitting in," derived from its
// status — this is the dashboard's graph-state visualization. There's no
// separate API field for this: status already carries enough information,
// and inventing a parallel "current_node" field the backend would have to
// keep in sync would be duplication for no real benefit.
export const GRAPH_NODES = [
  "gather_context",
  "hypothesize",
  "classify_risk",
  "evaluate_policy",
  "decide",
] as const;

export function currentGraphNode(status: string): (typeof GRAPH_NODES)[number] | null {
  switch (status) {
    case "open":
      return null;
    case "running":
      return "gather_context";
    case "awaiting_approval":
      return "evaluate_policy";
    case "diagnosed":
    case "incomplete_step_ceiling":
    case "incomplete_cost_ceiling":
    case "incomplete_provider_error":
      return "decide";
    default:
      return null;
  }
}

export type DeterministicScores = {
  reached_diagnosis: boolean;
  policy_verdict_correct: boolean | null;
  recommended_action_exact_match: boolean | null;
  evidence_grounded_heuristic: boolean | null;
  tool_selection_score: number;
  unnecessary_tool_call_count: number;
  argument_error_count: number;
  steps_used: number;
  estimated_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  latency_seconds: number;
};

export type JudgeScores = {
  diagnosis_accuracy: number;
  diagnosis_accuracy_reasoning: string;
  evidence_groundedness: number;
  evidence_groundedness_reasoning: string;
  remediation_quality: number;
  remediation_quality_reasoning: string;
  escalation_correct: boolean;
  escalation_reasoning: string;
};

export type EvalScenarioResult = {
  scenario_key: string;
  status: string;
  deterministic: DeterministicScores;
  judge: JudgeScores | null;
  judge_error: string | null;
  langfuse_trace_id: string | null;
  langfuse_trace_url: string | null;
};

export type AggregateScores = {
  scenario_count: number;
  completion_rate: number;
  policy_verdict_accuracy: number;
  recommended_action_exact_match_rate: number;
  mean_tool_selection_score: number;
  total_unnecessary_tool_calls: number;
  total_argument_errors: number;
  mean_diagnosis_accuracy: number | null;
  mean_evidence_groundedness: number | null;
  mean_remediation_quality: number | null;
  escalation_correctness_rate: number | null;
  mean_steps_used: number;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  mean_latency_seconds: number;
};

export type EvalRunSummary = {
  run_label: string;
  started_at: string;
  scenario_count: number;
  completion_rate: number;
  policy_verdict_accuracy: number;
  mean_diagnosis_accuracy: number | null;
  total_cost_usd: number;
  mean_latency_seconds: number;
};

export type EvalRun = {
  run_label: string;
  started_at: string;
  scenario_keys: string[];
  scenarios: EvalScenarioResult[];
  aggregate: AggregateScores;
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
  decideApproval: (
    id: number,
    decision: { approved: boolean; actor: string; params?: Record<string, unknown> }
  ) =>
    apiFetch<Incident>(`/api/v1/incidents/${id}/approvals`, {
      method: "POST",
      body: JSON.stringify(decision),
    }),
  listEvalRuns: () => apiFetch<EvalRunSummary[]>("/api/v1/eval-runs"),
  getEvalRun: (label: string) => apiFetch<EvalRun>(`/api/v1/eval-runs/${label}`),
  listServices: () => apiFetch<Service[]>("/api/v1/services"),
  getServiceMemory: (serviceName: string) =>
    apiFetch<ServiceMemoryEntry[]>(`/api/v1/services/${encodeURIComponent(serviceName)}/memory`),
  deleteMemoryEntry: (id: number) =>
    apiFetch<void>(`/api/v1/memory/${id}`, { method: "DELETE" }),
};
