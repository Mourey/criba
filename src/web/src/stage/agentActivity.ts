// The Demo stage's Agent activity (ADR 0075): one block per Candidate turn, built only from stored records.
// The detail's messages and events, the trace's runs and Jev calls (getToolCalls) and Jev's readings
// (getJevReadings). Nothing is inferred beyond placing each record in the turn whose time window holds it:
// a turn runs from one inbound Candidate message to the next. No message text is ever read here.

import type { AppEvent, JevReadingEntry, Message, ToolCallRun, TraceJevCall } from "../api/types.ts";

export interface Turn {
  /** The Candidate message that opened the turn; null for what happened before the first one. */
  inbound: Message | null;
  reading: JevReadingEntry | null;
  jevCalls: TraceJevCall[];
  runs: ToolCallRun[];
  events: AppEvent[];
  replies: Message[];
}

/** The events a viewer reads as "what code did after the agent's call". Other kinds stay on the record's own list. */
export const CODE_EVENTS: ReadonlySet<AppEvent["kind"]> = new Set<AppEvent["kind"]>([
  "answer_recorded",
  "answer_invalid",
  "answer_ambiguous",
  "stage_changed",
  "reopened",
  "screening_finished",
  "knockout",
  "summary_written",
  "approval_requested",
  "approved",
  "escalation_raised",
  "tone_hint",
  "run_error",
  "slots_offered",
  "booking_link_sent",
  "interview_booked",
  "withdrawn",
  "opted_out",
]);

const time = (iso: string | null | undefined): number => {
  const t = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
};

/** When a run started: the earlier of its first call and its usage time (written when the run returns). */
export function runStart(run: ToolCallRun): string | null {
  const first = run.calls[0]?.started_at ?? null;
  if (first && run.at) return time(first) <= time(run.at) ? first : run.at;
  return first ?? run.at ?? null;
}

export function runTime(run: ToolCallRun): number {
  return time(runStart(run));
}

const isInbound = (m: Message) => m.direction === "inbound" && m.sender === "candidate";

/**
 * The turns, oldest first. A record whose time is before the first Candidate message goes in a leading turn
 * with no inbound message, which is left out when it is empty. Runs are placed by `runTime`, Jev calls by their
 * time, events by `created_at` and outbound messages by `created_at`; readings attach by message id.
 */
export function buildTurns(input: {
  messages: readonly Message[];
  events: readonly AppEvent[];
  runs: readonly ToolCallRun[];
  jevCalls: readonly TraceJevCall[];
  readings: readonly JevReadingEntry[];
}): Turn[] {
  const ordered = [...input.messages].sort((a, b) => time(a.created_at) - time(b.created_at) || a.id - b.id);
  const inbound = ordered.filter(isInbound);
  const turns: Turn[] = [null, ...inbound].map((m) => ({
    inbound: m,
    reading: m ? (input.readings.find((r) => r.message_id === m.id) ?? null) : null,
    jevCalls: [],
    runs: [],
    events: [],
    replies: [],
  }));
  const starts = inbound.map((m) => time(m.created_at));
  // The turn index for a time: the last inbound message at or before it, or the leading turn.
  const turnAt = (t: number) => {
    let index = 0;
    for (let i = 0; i < starts.length; i += 1) if (starts[i] <= t) index = i + 1;
    return turns[index];
  };
  for (const run of input.runs) {
    if (run.run_id === null) continue;
    turnAt(runTime(run)).runs.push(run);
  }
  for (const call of input.jevCalls) turnAt(time(call.at)).jevCalls.push(call);
  for (const event of [...input.events].sort((a, b) => time(a.created_at) - time(b.created_at) || a.id - b.id)) {
    if (CODE_EVENTS.has(event.kind)) turnAt(time(event.created_at)).events.push(event);
  }
  for (const m of ordered) if (!isInbound(m) && m.direction === "outbound") turnAt(time(m.created_at)).replies.push(m);
  for (const turn of turns) turn.runs.sort((a, b) => runTime(a) - runTime(b));
  const opening = turns[0];
  const openingEmpty = opening.runs.length + opening.events.length + opening.replies.length + opening.jevCalls.length === 0;
  return openingEmpty ? turns.slice(1) : turns;
}

/** A tool call's arguments as `name=value` pairs: the stored canonical values only (codes, ids, numbers), never text. */
export function argsText(args: Record<string, unknown> | undefined): string {
  return Object.entries(args ?? {})
    .map(([name, value]) => {
      const scalar = (v: unknown) => typeof v === "string" || typeof v === "number" || typeof v === "boolean";
      if (scalar(value)) return `${name}=${String(value)}`;
      if (Array.isArray(value) && value.every(scalar)) return `${name}=[${value.join(", ")}]`;
      return `${name}=-`;
    })
    .join(", ");
}
