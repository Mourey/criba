import type { ReactNode } from "react";

export type TagTone = "neutral" | "live" | "warn" | "info" | "success";

// A flag or attribute on a row: Takeover, Stalled, "Full-time". Stages and outcomes are StatusBadge.
export function Tag({ tone = "neutral", children }: { tone?: TagTone; children: ReactNode }) {
  return <span className={`ui-tag ui-tag--${tone}`}>{children}</span>;
}
