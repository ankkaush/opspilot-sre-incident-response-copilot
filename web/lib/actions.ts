"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { api } from "./api";

export async function createAndRunIncident(formData: FormData) {
  const scenarioKey = formData.get("scenario_key");
  if (typeof scenarioKey !== "string" || scenarioKey.length === 0) {
    throw new Error("scenario_key is required.");
  }

  const incident = await api.createIncident(scenarioKey);
  await api.runIncident(incident.id);

  revalidatePath("/");
  redirect(`/incidents/${incident.id}`);
}

export async function decideApproval(formData: FormData) {
  const incidentId = Number(formData.get("incident_id"));
  if (!Number.isFinite(incidentId)) {
    throw new Error("incident_id is required.");
  }

  const approved = formData.get("decision") === "approve";
  const actor = formData.get("actor");
  if (typeof actor !== "string" || actor.trim().length === 0) {
    throw new Error("Your name is required so the decision can be audited.");
  }

  const actionType = formData.get("action_type");
  let params: Record<string, unknown> | undefined;
  if (approved && actionType === "rollback_deployment") {
    const targetVersion = formData.get("target_version");
    if (typeof targetVersion !== "string" || targetVersion.trim().length === 0) {
      throw new Error("A target version is required to approve a rollback.");
    }
    params = { target_version: targetVersion };
  } else if (approved && actionType === "toggle_feature_flag") {
    const flagName = formData.get("flag_name");
    if (typeof flagName !== "string" || flagName.trim().length === 0) {
      throw new Error("A flag name is required to approve a feature-flag toggle.");
    }
    params = { flag_name: flagName, enabled: formData.get("enabled") === "on" };
  }

  await api.decideApproval(incidentId, { approved, actor, params });

  revalidatePath(`/incidents/${incidentId}`);
  revalidatePath("/");
}
