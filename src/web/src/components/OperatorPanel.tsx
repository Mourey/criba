import { useQuery } from "@tanstack/react-query";
import { type CSSProperties, useId } from "react";
import { api } from "../api/client.ts";
import type { AppEvent, ApplicationDetail, JevReading, JevReadingEntry, JevThresholds, MessageSignal, ToolCall, ToolCallRun } from "../api/types.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { formatDateTime, formatMs, formatNumber, formatUsd } from "../lib/format.ts";
import { stageLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/operator.ts";
import "../styles/surfaces/operator.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "./ui/Button.tsx";
import { Card } from "./ui/Card.tsx";
import { Tag } from "./ui/Tag.tsx";
import "./ui/ui.css";

/**
 * One `run_error` event, read through the only three keys the panel may show. The event's data allows
 * extra keys (contracts/api.openapi.yaml EventData_run_error), and none of them is ever read here.
 */
interface RunErrorLine {
  id: number;
  at: string;
  kind: string | null;
  runId: string | null;
  systemTurn: boolean | null;
}

function runErrorLine(e: AppEvent): RunErrorLine {
  const data = e.data as { kind?: unknown; run_id?: unknown; system_turn?: unknown };
  return {
    id: e.id,
    at: e.created_at,
    kind: typeof data.kind === "string" ? data.kind : null,
    runId: typeof data.run_id === "string" ? data.run_id : null,
    systemTurn: typeof data.system_turn === "boolean" ? data.system_turn : null,
  };
}

/** The detail the panel reads; the Demo stage passes it with `cost` dropped. */
type PanelDetail = Omit<ApplicationDetail, "cost"> & { cost?: ApplicationDetail["cost"] };

/** Transcript order: by the message's position in the loaded Transcript; entries whose message is not
 * loaded follow, by `at`. */
function inTranscriptOrder<T extends { message_id: number; at: string }>(entries: readonly T[], position: ReadonlyMap<number, number>): T[] {
  return [...entries].sort((a, b) => {
    const pa = position.get(a.message_id);
    const pb = position.get(b.message_id);
    if (pa !== undefined && pb !== undefined) return pa - pb;
    if (pa !== undefined) return -1;
    if (pb !== undefined) return 1;
    return a.at.localeCompare(b.at);
  });
}

const score = (n: number) => n.toFixed(2);
const unit = (n: number) => Math.min(1, Math.max(0, n));

interface Marker {
  name: string | null;
  value: number;
}

/**
 * The one place a threshold value is read: it positions a marker and words its label, nothing more. A flag
 * comes from the stored `fired`, never from a score against a threshold (ADR 0051). Null unless the reading's
 * policy version is the one the response's thresholds belong to; an older version has no thresholds to draw.
 */
function appliedThresholds(r: JevReading, t: JevThresholds): { bars: Record<copy.Bar, Marker[]>; language: number } | null {
  if (r.policy_version !== t.version) return null;
  return {
    bars: {
      switch: [{ name: null, value: t.switch_noul }],
      frustrated: [{ name: null, value: t.frustrated }],
      confused: [{ name: null, value: t.confused }],
      wants_human: [
        { name: copy.markerHint, value: t.wants_human_hint },
        { name: copy.markerEscalation, value: t.wants_human_escalate },
      ],
      abusive: [{ name: null, value: t.abusive_escalate }],
    },
    language: r.first_message ? t.first_confidence : t.switch_confidence,
  };
}

const markerTexts = (markers: readonly Marker[]) => markers.map((m) => copy.markerText(m.name, score(m.value)));

/** One score on one line: its name, a track with the fill and markers drawn from the value, and the value as text.
 * `fired` is the stored flag for this bar, never a score against a threshold. */
function ScoreBar({ name, label, value, markers, fired, plain }: { name: string; label: string; value: number; markers: readonly Marker[]; fired: boolean; plain?: boolean }) {
  const texts = markerTexts(markers);
  return (
    <div
      className={fired ? "op-bar op-bar--fired" : plain ? "op-bar op-bar--plain" : "op-bar"}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={value}
    >
      <span className="op-bar__name">{name}</span>
      <span className="op-bar__track">
        <span className="op-bar__fill" style={{ "--v": unit(value) } as CSSProperties} /* geometry */ />
        {markers.map((m, i) => (
          <span
            key={m.name ?? i}
            className={m.name === copy.markerHint ? "op-bar__marker op-bar__marker--hint" : "op-bar__marker"}
            title={texts[i]}
            style={{ "--at": unit(m.value) } as CSSProperties} /* geometry */
          >
            <span className="ui-sr-only">{texts[i]}</span>
          </span>
        ))}
      </span>
      <span className="op-bar__value">{score(value)}</span>
    </div>
  );
}

const isScore = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The intent as the panel may show it: one of the six codes and a finite confidence. Anything else (a text, a null, an absent key) is no reading. */
function presentIntent(r: JevReading): { choice: copy.Intent; confidence: number } | null {
  const i: unknown = r.intent;
  if (typeof i !== "object" || i === null) return null;
  const { choice, confidence } = i as { choice?: unknown; confidence?: unknown };
  const known = copy.INTENTS.find((c) => c === choice);
  return known && isScore(confidence) ? { choice: known, confidence } : null;
}

/** What Jev stored for one message: the language, the intent line and the bars (five, and a sixth for the Manipulation score) for an ok reading, each of the intent and the Manipulation score only when present and one muted line when neither is, or what the status means. */
function ReadingBody({ entry, thresholds }: { entry: JevReadingEntry; thresholds: JevThresholds }) {
  const r = entry.reading;
  if (entry.status !== "ok") return <p className="op-panel__muted">{copy.readingStatusLine[entry.status]}</p>;
  if (!r) return null;
  const applied = appliedThresholds(r, thresholds);
  const intent = presentIntent(r);
  const manipulation: unknown = r.malice;
  const manipulationScore = isScore(manipulation) ? manipulation : null;
  return (
    <>
      <p className="op-signal__line">{copy.languageText(r.lang, score(r.lang_confidence), applied && score(applied.language))}</p>
      {intent ? <p className="op-signal__line">{copy.intentLine(copy.intentLabel[intent.choice], score(intent.confidence))}</p> : manipulationScore === null && <p className="op-panel__muted">{copy.intentNone}</p>}
      <div className="op-bars">
        {copy.BARS.map((b) => (
          <ScoreBar
            key={b}
            name={copy.barLabel[b]}
            label={copy.barText(b, score(r[b]), markerTexts(applied?.bars[b] ?? []))}
            value={r[b]}
            markers={applied?.bars[b] ?? []}
            fired={b === "switch" ? r.language_set !== null && !r.first_message : r.fired.includes(b)}
          />
        ))}
        {manipulationScore !== null && <ScoreBar name={copy.manipulationLabel} label={copy.manipulationText(score(manipulationScore))} value={manipulationScore} markers={[]} fired={false} plain />}
      </div>
      {!applied && <p className="op-panel__muted">{copy.thresholdsUnavailable(r.policy_version)}</p>}
      {r.hint && <p className="op-signal__line">{copy.hintLine(r.hint)}</p>}
      <p className="op-panel__muted">{copy.modelLine(r.model, r.policy_version)}</p>
    </>
  );
}

/** A row of the readings list: Jev's reading of a typed message, or the flag entry of a message with none. */
type ListRow = { message_id: number; at: string } & ({ reading: JevReadingEntry; entry?: undefined } | { entry: MessageSignal; reading?: undefined });

export type SignalRowProps = {
  inTranscript: boolean;
  /** Absent where there is no Transcript to jump to (the Demo stage): the row then has no jump button. */
  onShow?: (messageId: number) => void;
} & (
  | { entry: MessageSignal; reading?: undefined; thresholds?: undefined }
  /** Jev's full reading of the message, from the readings endpoint, in place of the flag-only entry. */
  | { entry?: undefined; reading: JevReadingEntry; thresholds: JevThresholds }
);

/** One typed Candidate message: when, at which Stage, what fired. Never its text. */
export function SignalRow({ entry, reading, thresholds, inTranscript, onShow }: SignalRowProps) {
  const head = entry ?? reading;
  const r = reading?.reading ?? null;
  const flags = entry ?? (r && { fired: r.fired, language_changed: r.language_set !== null && !r.first_message, escalated: r.escalation !== null });
  return (
    <li className="op-signal">
      <div className="op-signal__head">
        <div className="op-signal__meta">
          <time className="op-signal__time" dateTime={head.at}>
            {formatDateTime(head.at)}
          </time>
          <span className="op-panel__muted">{head.stage ? stageLabel[head.stage] : "-"}</span>
          {flags?.fired.map((s) => (
            <Tag key={s} tone="warn">
              {copy.signalLabel[s]}
            </Tag>
          ))}
          {flags?.language_changed && <Tag tone="info">{copy.panelLanguageChanged}</Tag>}
          {flags?.escalated && <Tag tone="warn">{copy.panelEscalated}</Tag>}
        </div>
        {onShow && (
          <Button
            variant="quiet"
            className="op-signal__jump"
            disabled={!inTranscript}
            title={inTranscript ? undefined : copy.panelNotInTranscript}
            onClick={() => onShow(head.message_id)}
          >
            {copy.panelShowInTranscript}
          </Button>
        )}
      </div>
      {reading && <ReadingBody entry={reading} thresholds={thresholds} />}
    </li>
  );
}

/** A stored argument value as text: a string, number or boolean as it is, a list joined by ", ", anything else (a null) as not stored. */
function argText(value: unknown): string {
  const scalar = (v: unknown) => typeof v === "string" || typeof v === "number" || typeof v === "boolean";
  if (scalar(value)) return String(value);
  if (Array.isArray(value) && value.every(scalar)) return value.join(", ");
  return copy.traceNotStored;
}

// Neutral for ok: the success tone fails axe colour contrast in the light theme. The text carries the meaning.
const outcomeTone = { ok: "neutral", invalid: "warn", knockout: "info", error: "warn" } as const;
const statusTone = { ok: "neutral", error: "warn", timeout: "warn" } as const;
const costLabel = "Cost";

/** One tool call: its name, start, duration and outcome as text in a Tag, the error code when stored, then the argument names with their canonical values. */
function TraceCall({ call }: { call: ToolCall }) {
  const args = Object.entries(call.args ?? {});
  return (
    <li className="op-call">
      <div className="op-call__head">
        <span className="op-call__tool">{call.tool}</span>
        <Tag tone={outcomeTone[call.outcome]}>{copy.traceOutcomeLabel[call.outcome]}</Tag>
        <div className="op-call__meta">
          <time className="op-call__time" dateTime={call.started_at}>
            {formatDateTime(call.started_at)}
          </time>
          <span className="op-call__ms">{formatMs(call.duration_ms)}</span>
          {call.error && <span className="op-call__error">{call.error}</span>}
        </div>
      </div>
      {args.length > 0 && (
        <dl className="op-call__args">
          {args.map(([name, value]) => (
            <div key={name} className="op-call__arg">
              <dt>{name}</dt>
              <dd>{argText(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

/** One run with its numbers and calls, or the final group of calls no run is recorded for (no numbers, no jump). */
function TraceRun({ run, position, onShow, economics }: { run: ToolCallRun; position: ReadonlyMap<number, number>; onShow?: (messageId: number) => void; economics: boolean }) {
  const runId = run.run_id;
  const reply = run.reply_message_ids[0];
  const canShow = reply !== undefined && position.has(reply);
  return (
    <li className="op-run">
      <h4 className="op-run__title">
        {runId === null ? (
          copy.traceRunNotRecorded
        ) : (
          <>
            <span className="op-run__prefix">{copy.panelRun(runId).slice(0, copy.panelRun(runId).length - runId.length).trimEnd()}</span>{" "}
            <span className="op-run__idtext">{runId}</span>
          </>
        )}
      </h4>
      {(run.status || runId !== null) && (
        <div className="op-run__head">
          {run.status && <Tag tone={statusTone[run.status]}>{copy.traceStatusLabel[run.status]}</Tag>}
          {runId !== null && onShow && (
            <Button
              variant="quiet"
              className="op-signal__jump"
              disabled={!canShow}
              title={canShow ? undefined : reply === undefined ? copy.traceNoReply : copy.panelNotInTranscript}
              onClick={() => {
                if (reply !== undefined) onShow(reply);
              }}
            >
              {copy.traceShowReply}
            </Button>
          )}
        </div>
      )}
      {runId !== null && economics && (
        <div className="op-run__stats">
          <span>{`${copy.traceTokensIn} ${formatNumber(run.input_tokens)}`}</span>
          <span>{`${copy.traceTokensOut} ${formatNumber(run.output_tokens)}`}</span>
          <span>{`${costLabel} ${formatUsd(run.cost_usd)}`}</span>
          <span>{`${copy.traceLatency} ${formatMs(run.latency_ms)}`}</span>
        </div>
      )}
      <ol className="op-calls" aria-label={copy.traceCallsLabel}>
        {run.calls.map((call, i) => (
          <TraceCall key={`${i}:${call.started_at}`} call={call} />
        ))}
      </ol>
    </li>
  );
}

/**
 * The "Agent runs" section (AT7): the Application's agent runs and their tool calls, oldest first, from the trace
 * endpoint. Enabled only for the effective role `operator`. The key includes the message and event counts, so a
 * streamed message or event refetches; the section keeps the previous runs of the same Application meanwhile.
 * Reads the typed fields only: never a message, the model's thinking, a hint or a tool result.
 */
function AgentTrace({
  detail,
  position,
  onShowMessage,
  economics,
}: {
  detail: PanelDetail;
  position: ReadonlyMap<number, number>;
  onShowMessage?: (messageId: number) => void;
  economics: boolean;
}) {
  const titleId = useId();
  const { role } = useAuth();
  const trace = useQuery({
    queryKey: ["tool-calls", detail.id, detail.messages.length, detail.events.length],
    queryFn: () => api.getToolCalls(detail.id),
    enabled: canSeeEconomics(role),
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === detail.id ? previous : undefined),
  });
  return (
    <section className="op-panel__block" aria-labelledby={titleId}>
      <h3 id={titleId} className="op-panel__heading">
        {copy.traceTitle}
      </h3>
      {trace.data &&
        (trace.data.runs.length === 0 ? (
          <p className="op-panel__muted">{copy.traceNone}</p>
        ) : (
          <ol className="op-runs" aria-label={copy.traceRunsLabel}>
            {trace.data.runs.map((run) => (
              <TraceRun key={run.run_id ?? "not-recorded"} run={run} position={position} onShow={onShowMessage} economics={economics} />
            ))}
          </ol>
        ))}
      {trace.isError && !trace.data && (
        <div className="op-panel__retry">
          <p role="status" className="op-panel__muted">
            {copy.traceUnavailable}
          </p>
          <Button variant="quiet" aria-label={copy.traceRetryLabel} onClick={() => void trace.refetch()}>
            {copy.traceRetry}
          </Button>
        </div>
      )}
    </section>
  );
}

/**
 * The Operator-only fourth column of the Application page (UI8, UI9). Besides the detail response already in
 * the cache it makes two requests, Jev's readings of the Application (JR4) and the agent trace (AT7), each enabled only
 * for the effective role `operator`; when the readings fail the flag rows built from the detail stay, and when the trace
 * fails the rest of the panel stays. The page renders the panel only for that role. The Demo stage renders it too
 * (ADR 0075), with no `onShowMessage` (no Transcript there, so no jump buttons) and `economics` off, so no token,
 * cost or latency figure reaches a recording (ADR 0019).
 */
export function OperatorPanel({
  id,
  detail,
  onShowMessage,
  economics = true,
}: {
  id: string;
  detail: PanelDetail;
  onShowMessage?: (messageId: number) => void;
  economics?: boolean;
}) {
  const titleId = useId();
  const signalsId = useId();
  const errorsId = useId();
  const { role } = useAuth();
  // Keyed on the message count, so a streamed message refetches without blanking the rows on screen.
  const readings = useQuery({
    queryKey: ["jev-readings", detail.id, detail.messages.length],
    queryFn: () => api.getJevReadings(detail.id),
    enabled: canSeeEconomics(role),
    // Keep the rows on screen while a streamed message refetches, but never another Application's.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === detail.id ? previous : undefined),
  });
  const signals = detail.message_signals ?? null;
  const position = new Map(detail.messages.map((m, i) => [m.id, i]));
  const rows = signals ? inTranscriptOrder(signals, position) : null;
  // Once the readings load, a flag entry with no reading (a spoken turn: the readings cover typed messages only)
  // stays as a flag-only row among the reading rows.
  const readingRows = readings.data
    ? inTranscriptOrder<ListRow>(
        [
          ...readings.data.readings.map((reading) => ({ message_id: reading.message_id, at: reading.at, reading })),
          ...(rows ?? [])
            .filter((entry) => !readings.data.readings.some((r) => r.message_id === entry.message_id))
            .map((entry) => ({ message_id: entry.message_id, at: entry.at, entry })),
        ],
        position,
      )
    : null;
  const okReadings = readings.data ? readings.data.readings.flatMap((e) => (e.status === "ok" && e.reading ? [e.reading] : [])) : null;
  // A tick is drawn only for an ok reading whose policy version is the response's thresholds' version.
  const ticksDrawn = !!readings.data && !!okReadings?.some((r) => r.policy_version === readings.data.thresholds.version);
  // The counts stay on the flag entries, which also cover spoken turns; the readings (typed messages only) feed the peaks.
  const count = (s: copy.Signal) => (rows ?? okReadings ?? []).filter((r) => r.fired.includes(s)).length;
  const peak = (s: copy.Signal) => (okReadings && okReadings.length > 0 ? score(Math.max(...okReadings.map((r) => r[s]))) : "-");
  // The highest Manipulation score over the ok readings that carry one; a dash when none does.
  const manipulationScores = (okReadings ?? []).flatMap((r) => {
    const m: unknown = r.malice;
    return isScore(m) ? [m] : [];
  });
  const manipulationPeak = manipulationScores.length > 0 ? score(Math.max(...manipulationScores)) : "-";
  const runErrors = detail.events.filter((e) => e.kind === "run_error").map(runErrorLine);
  const cost = detail.cost;

  return (
    <section id={id} className="detail-col op-panel" aria-labelledby={titleId} tabIndex={0}>
      <h2 id={titleId} className="op-panel__title">
        {copy.panelTitle}
      </h2>
      <p className="op-panel__lede">{copy.panelFixedLine}</p>
      {ticksDrawn && <p className="op-panel__muted">{copy.readingsLegend}</p>}

      <section className="op-panel__block" aria-labelledby={signalsId}>
        <h3 id={signalsId} className="op-panel__heading">
          {copy.panelSignalsTitle}
        </h3>
        {rows === null && readingRows === null ? (
          <p className="op-panel__muted">{copy.panelSignalsAbsent}</p>
        ) : (
          <>
            <div className="op-panel__strips">
              <p className="op-panel__caption op-panel__caption--count" aria-hidden="true">
                {copy.panelCountColumn}
              </p>
              {okReadings && (
                <p className="op-panel__caption op-panel__caption--peak" aria-hidden="true">
                  {copy.readingsPeakColumn}
                </p>
              )}
              <ul className="op-panel__counts" aria-label={copy.panelCountsLabel}>
                {copy.SIGNALS.map((s) => (
                  <li key={s}>
                    <span>{copy.signalLabel[s]}</span> <b>{count(s)}</b>
                  </li>
                ))}
                {okReadings && (
                  <li>
                    <span>{copy.manipulationLabel}</span> <b>-</b>
                  </li>
                )}
              </ul>
              {okReadings && (
                <ul className="op-panel__counts op-panel__counts--peaks" aria-label={copy.readingsPeaksLabel}>
                  {copy.SIGNALS.map((s) => (
                    <li key={s}>
                      <span className="ui-sr-only">{copy.signalLabel[s]}</span> <b>{peak(s)}</b>
                    </li>
                  ))}
                  <li>
                    <span className="ui-sr-only">{copy.manipulationLabel}</span> <b>{manipulationPeak}</b>
                  </li>
                </ul>
              )}
            </div>
            {readings.data && readingRows ? (
              readingRows.length === 0 ? (
                <p className="op-panel__muted">{copy.panelSignalsNone}</p>
              ) : (
                <ol className="op-panel__rows" aria-label={copy.readingsListLabel}>
                  {readingRows.map((row) =>
                    row.reading ? (
                      <SignalRow key={row.message_id} reading={row.reading} thresholds={readings.data.thresholds} inTranscript={position.has(row.message_id)} onShow={onShowMessage} />
                    ) : (
                      <SignalRow key={row.message_id} entry={row.entry} inTranscript={position.has(row.message_id)} onShow={onShowMessage} />
                    ),
                  )}
                </ol>
              )
            ) : rows && rows.length === 0 ? (
              <p className="op-panel__muted">{copy.panelSignalsNone}</p>
            ) : (
              <ol className="op-panel__rows" aria-label={copy.panelRowsLabel}>
                {rows?.map((entry) => (
                  <SignalRow key={entry.message_id} entry={entry} inTranscript={position.has(entry.message_id)} onShow={onShowMessage} />
                ))}
              </ol>
            )}
          </>
        )}
        {readings.isError && !readings.data && (
          <div className="op-panel__retry">
            <p className="op-panel__muted">{copy.readingsUnavailable}</p>
            <Button variant="quiet" onClick={() => void readings.refetch()}>
              {copy.readingsRetry}
            </Button>
          </div>
        )}
      </section>

      <section className="op-panel__block" aria-labelledby={errorsId}>
        <h3 id={errorsId} className="op-panel__heading">
          {copy.panelRunErrorsTitle}
        </h3>
        {runErrors.length === 0 ? (
          <p className="op-panel__muted">{copy.panelRunErrorsNone}</p>
        ) : (
          <ul className="op-panel__errors">
            {runErrors.map((r) => (
              <li key={r.id}>
                <time dateTime={r.at}>{formatDateTime(r.at)}</time> · {(r.kind && copy.runErrorKindLabel[r.kind]) ?? "-"} ·{" "}
                {r.systemTurn ? copy.panelSystemTurn : copy.panelCandidateTurn} ·{" "}
                <span className="op-panel__mono">{copy.panelRun(r.runId ?? "-")}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <AgentTrace detail={detail} position={position} onShowMessage={onShowMessage} economics={economics} />

      {economics && cost && (
        <Card title="Cost (Operators only)" level={3}>
          <dl className="stack detail-dl">
            <div className="row spread">
              <dt className="detail-muted">Estimated cost</dt>
              <dd className="detail-dd">{formatUsd(cost.est_cost_usd)}</dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Reconciled cost</dt>
              <dd className="detail-dd">{formatUsd(cost.reconciled_cost_usd)}</dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Agent runs</dt>
              <dd className="detail-dd">{formatNumber(cost.runs)}</dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Tokens in / out</dt>
              <dd className="detail-dd">
                {formatNumber(cost.input_tokens)} / {formatNumber(cost.output_tokens)}
              </dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Cache read / write</dt>
              <dd className="detail-dd">
                {formatNumber(cost.cache_read_tokens)} / {formatNumber(cost.cache_write_tokens)}
              </dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Median latency</dt>
              <dd className="detail-dd">{formatMs(cost.p50_latency_ms)}</dd>
            </div>
            <div className="row spread">
              <dt className="detail-muted">Models</dt>
              <dd className="detail-dd">{cost.models?.join(", ") || "-"}</dd>
            </div>
          </dl>
        </Card>
      )}
    </section>
  );
}
