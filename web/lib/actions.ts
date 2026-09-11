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
