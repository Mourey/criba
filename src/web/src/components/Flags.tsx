import type { Flag, Outcome, Stage } from "../api/types.ts";
import { flagLabel, flagNeedsAttention, stageLabel } from "../lib/labels.ts";
import { formatDuration } from "../lib/format.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { StatusBadge } from "./ui/StatusBadge.tsx";
import { Tag } from "./ui/Tag.tsx";
import "./ui/ui.css";

// Stages whose StatusBadge text is the stageLabel text. Every other stage, interview_booked included
// (its badge reads "Interview", the label "Interview booked"), stays a Tag so no visible text changes.
const BADGE_STAGES: Partial<Record<Stage, "qualified" | "approved">> = { qualified: "qualified", approved: "approved" };

export function FlagTags({ flags }: { flags: readonly Flag[] }) {
  if (flags.length === 0) return <span className="muted">-</span>;
  return (
    <span className="tag-row">
      {flags.map((flag) => (
        <Tag key={flag} tone={flagNeedsAttention[flag] ? "warn" : "neutral"}>
          {flagLabel[flag]}
        </Tag>
      ))}
    </span>
  );
}

export function StageOutcome({ stage, outcome }: { stage: Stage; outcome: Outcome | null }) {
  const badge = BADGE_STAGES[stage];
  return (
    <span className="tag-row">
      {badge ? <StatusBadge status={badge} /> : <Tag>{stageLabel[stage]}</Tag>}
      {outcome && <StatusBadge status={outcome} />}
    </span>
  );
}

/** Time per step as a segmented bar, with the exact figures in the accessible name and title. */
export function StepTimes({ durations, order }: { durations: Record<string, number> | undefined; order: readonly Stage[] }) {
  const entries = order.filter((s) => (durations?.[s] ?? 0) > 0).map((s) => [s, durations![s]] as const);
  if (entries.length === 0) return <span className="muted">-</span>;
  const total = entries.reduce((sum, [, v]) => sum + v, 0);
  const text = entries.map(([s, v]) => `${stageLabel[s]} ${formatDuration(v)}`).join(", ");
  return (
    <div className="steps" role="img" aria-label={`Time per step: ${text}`} title={text}>
      {entries.map(([s, v]) => (
        <span key={s} style={{ flexGrow: v / total }} /* geometry */ />
      ))}
    </div>
  );
}
