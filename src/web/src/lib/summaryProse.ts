// The state of a summary's written part (ADR 0072). The backend records it in the structured part,
// which the contract keeps open for the summary writer's own fields: `prose_status` is "retrying"
// while it asks the model again, "failed" once it has stopped, and absent otherwise.
import type { Summary } from "../api/types.ts";

export type ProseState = "retrying" | "failed";

/** Why a summary has no written part, or null when it has one or none was asked for. */
export function proseState(summary: Summary | null | undefined): ProseState | null {
  if (!summary || summary.prose) return null;
  const status = summary.structured.prose_status;
  return status === "retrying" || status === "failed" ? status : null;
}

export const proseStateCopy: Record<ProseState, string> = {
  retrying: "The written summary is not ready yet. It is retried automatically; the answers are complete.",
  failed: "The written summary could not be produced. The answers and the timeline are complete.",
};
