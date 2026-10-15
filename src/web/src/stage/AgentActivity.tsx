import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../api/client.ts";
import type { AppEvent, ApplicationDetail, JevReadingEntry, Message, Stage, ToolCall, ToolCallRun, TraceJevCall } from "../api/types.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { Button } from "../components/ui/Button.tsx";
import { formatMs, formatNumber, formatUsd } from "../lib/format.ts";
import { answerFieldLabel, eventLabel, stage as copy, stageLabel, titleCase } from "../lib/labels.ts";
import * as op from "../lib/labels/operator.ts";
import "../styles/surfaces/stage.css";
import { argsText, buildTurns, runStart, type Turn } from "./agentActivity.ts";
import { TIME_ZONE_OF, type PhoneCountry } from "./phone.ts";

type Detail = Omit<ApplicationDetail, "cost">;

/** HH:mm:ss in the conversation's zone: the timeline needs the seconds the record's HH:mm leaves out. */
function clock(iso: string, country: PhoneCountry): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE_OF[country] }).format(date);
}

const score = (n: number | null | undefined) => (typeof n === "number" && Number.isFinite(n) ? n.toFixed(2) : "-");

function messageKind(m: Message): string {
  if (m.content_type === "button_reply") return copy.activityKind.tap;
  if (m.modality === "spoken") return copy.activityKind.spoken;
  if (m.content_type === "media") return copy.activityKind.media;
  return copy.activityKind.typed;
}

function Step({ kind, at, country, children }: { kind: string; at?: string | null; country: PhoneCountry; children: React.ReactNode }) {
  return (
    <li className="activity-step" data-step={kind}>
      <span className="activity-step__time">{at ? clock(at, country) : ""}</span>
      <span className="activity-step__body">{children}</span>
    </li>
  );
}

/** Jev's reading of the turn's message: the full scores (an Operator view), then the call's own latency and model. */
function JevStep({ reading, calls, country }: { reading: JevReadingEntry | null; calls: TraceJevCall[]; country: PhoneCountry }) {
  const call = calls[0] ?? null;
  const r = reading?.status === "ok" ? reading.reading : null;
  const parts: string[] = [];
  if (r) {
    parts.push(`${op.languageName[r.lang]} ${score(r.lang_confidence)}`);
    if (r.language_set && !r.first_message) parts.push(op.panelLanguageChanged);
    for (const s of r.fired) parts.push(op.signalLabel[s as op.Signal] ?? s);
    const intent: unknown = r.intent;
    if (intent && typeof intent === "object") {
      const { choice, confidence } = intent as { choice?: unknown; confidence?: unknown };
      const known = op.INTENTS.find((c) => c === choice);
      if (known) parts.push(op.intentLine(op.intentLabel[known], score(typeof confidence === "number" ? confidence : null)));
    }
    const m: unknown = r.malice;
    if (typeof m === "number") parts.push(op.manipulationText(score(m)));
    if (r.hint) parts.push(op.hintLine(r.hint));
    if (r.escalation) parts.push(op.panelEscalated);
  } else if (reading) {
    parts.push(copy.activityJevStatus[reading.status as keyof typeof copy.activityJevStatus] ?? reading.status);
  }
  if (call) parts.push(`${formatMs(call.latency_ms)} · ${call.model}${call.status === "ok" ? "" : ` · ${call.status}`}`);
  if (parts.length === 0) return null;
  return (
    <Step kind="jev" at={call?.at} country={country}>
      <b>{copy.activityJev}</b> {parts.join(" · ")}
    </Step>
  );
}

function CallLine({ call, country }: { call: ToolCall; country: PhoneCountry }) {
  const outcome = copy.activityOutcome[call.outcome];
  return (
    <Step kind="tool" at={call.started_at} country={country}>
      <code className="activity-call">
        {call.tool}({argsText(call.args)})
      </code>{" "}
      → <span data-outcome={call.outcome}>{call.error ? `${outcome}: ${call.error}` : outcome}</span> · {formatMs(call.duration_ms)}
    </Step>
  );
}

function RunBlock({ run, country }: { run: ToolCallRun; country: PhoneCountry }) {
  const parts = [
    run.hermes_profile ?? null,
    run.model ?? null,
    run.input_tokens !== null && run.output_tokens !== null ? copy.activityTokens(formatNumber(run.input_tokens), formatNumber(run.output_tokens)) : null,
    run.latency_ms !== null ? formatMs(run.latency_ms) : null,
    run.cost_usd !== null ? formatUsd(run.cost_usd) : null,
    run.status ?? null,
  ].filter((p): p is string => p !== null);
  return (
    <>
      <Step kind="run" at={runStart(run)} country={country}>
        <b>{copy.activityRun}</b> {parts.join(" · ")}
      </Step>
      {run.calls.map((call, i) => (
        <CallLine key={`${i}:${call.started_at}`} call={call} country={country} />
      ))}
    </>
  );
}

/** What code wrote: the event's label and its codes, never text. An answer's zone shows by the record's Zone name. */
function eventText(event: AppEvent, detail: Detail): string {
  const data = event.data as Record<string, unknown>;
  const label = eventLabel[event.kind] ?? titleCase(event.kind);
  if (event.kind === "stage_changed" && typeof data.to === "string") return `${label} → ${stageLabel[data.to as Stage] ?? titleCase(data.to)}`;
  if (event.kind === "answer_recorded" && typeof data.field === "string") {
    const field = answerFieldLabel[data.field] ?? data.field;
    const value = data.value;
    const shown =
      data.field === "zone" && detail.zone && value === detail.zone.id
        ? detail.zone.name
        : typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : Array.isArray(value)
            ? value.join(", ")
            : null;
    return shown === null ? `${label}: ${field}` : `${label}: ${field} = ${shown}`;
  }
  if (event.kind === "knockout" && typeof data.criterion === "string") return `${label}: ${titleCase(data.criterion)}`;
  if (event.kind === "tone_hint" && typeof data.code === "string") return op.hintLine(data.code);
  return label;
}

function TurnBlock({ turn, detail, country, index }: { turn: Turn; detail: Detail; country: PhoneCountry; index: number }) {
  const m = turn.inbound;
  return (
    <li className="activity-turn" data-turn={index}>
      <ol className="activity-steps">
        {m ? (
          <Step kind="in" at={m.created_at} country={country}>
            <b>{copy.activityIn}</b> · {messageKind(m)}
          </Step>
        ) : (
          <Step kind="in" country={country}>
            <b>{copy.activityBefore}</b>
          </Step>
        )}
        {m && messageKind(m) !== copy.activityKind.tap ? (
          <JevStep reading={turn.reading} calls={turn.jevCalls} country={country} />
        ) : turn.jevCalls.length > 0 ? (
          <JevStep reading={null} calls={turn.jevCalls} country={country} />
        ) : null}
        {turn.runs.length === 0 && m ? (
          <Step kind="run" country={country}>
            <span className="activity-muted">{copy.activityNoRuns}</span>
          </Step>
        ) : null}
        {turn.runs.map((run) => (
          <RunBlock key={run.run_id ?? "none"} run={run} country={country} />
        ))}
        {turn.events.map((event) => (
          <Step key={event.id} kind="event" at={event.created_at} country={country}>
            <b>{copy.activityCode}</b> {eventText(event, detail)}
          </Step>
        ))}
        {turn.replies.map((reply) => (
          <Step key={reply.id} kind="out" at={reply.created_at} country={country}>
            <b>{copy.activityReply}</b>
            {reply.interactive ? ` · ${copy.activityButtons(reply.interactive.options.length)}` : ""}
          </Step>
        ))}
      </ol>
    </li>
  );
}

/**
 * Agent activity (ADR 0075): one block per Candidate turn, newest at the bottom, from the detail, the trace
 * (getToolCalls) and Jev's readings (getJevReadings). Both requests share the Operator panel's query keys, so
 * the two tabs ask once, and both refetch when a streamed message or event changes the detail. Operator only.
 */
export function AgentActivity({ detail, country }: { detail: Detail; country: PhoneCountry }) {
  const { role } = useAuth();
  const enabled = canSeeEconomics(role);
  const keep = <T,>(previous: T | undefined, previousQuery: { queryKey: readonly unknown[] } | undefined) =>
    previousQuery?.queryKey[1] === detail.id ? previous : undefined;
  const trace = useQuery({
    queryKey: ["tool-calls", detail.id, detail.messages.length, detail.events.length],
    queryFn: () => api.getToolCalls(detail.id),
    enabled,
    placeholderData: keep,
  });
  const readings = useQuery({
    queryKey: ["jev-readings", detail.id, detail.messages.length],
    queryFn: () => api.getJevReadings(detail.id),
    enabled,
    placeholderData: keep,
  });
  const root = useRef<HTMLDivElement>(null);
  const turns = buildTurns({
    messages: detail.messages,
    events: detail.events,
    runs: trace.data?.runs ?? [],
    jevCalls: trace.data?.jev_calls ?? [],
    readings: readings.data?.readings ?? [],
  });
  // Newest at the bottom: when a step is added, the scrolling region under the record follows it.
  const steps = turns.reduce((n, t) => n + t.runs.length + t.events.length + t.replies.length + t.jevCalls.length + 1, 0);
  useEffect(() => {
    const scroller = root.current?.closest<HTMLElement>(".stage-under");
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [steps]);
  if (!enabled) return null;
  return (
    <div ref={root} className="activity" data-testid="agent-activity">
      <p className="activity-legend">{copy.activityLegend}</p>
      {trace.isError && !trace.data ? (
        <div className="activity-retry">
          <p role="status" className="activity-muted">
            {copy.activityUnavailable}
          </p>
          <Button variant="quiet" onClick={() => void trace.refetch()}>
            {copy.activityRetry}
          </Button>
        </div>
      ) : null}
      {turns.length === 0 ? (
        <p className="activity-muted">{copy.activityEmpty}</p>
      ) : (
        <ol className="activity-turns" aria-label={copy.activityList}>
          {turns.map((turn, i) => (
            <TurnBlock key={turn.inbound?.id ?? "before"} turn={turn} detail={detail} country={country} index={i} />
          ))}
        </ol>
      )}
    </div>
  );
}
