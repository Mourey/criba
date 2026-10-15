import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useContext, useId, useState, type ReactNode } from "react";
import { api } from "../api/client.ts";
import type { AnswerStatus, Answers, ApplicationDetail, Outcome, Stage } from "../api/types.ts";
import { AuthContext } from "../auth/context.ts";
import { MessageLabelList } from "../components/MessageLabels.tsx";
import { AgentActivity } from "./AgentActivity.tsx";
import { OperatorPanel } from "../components/OperatorPanel.tsx";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Icon } from "../components/ui/Icon.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { StatusBadge, type StatusKind } from "../components/ui/StatusBadge.tsx";
import { Tabs } from "../components/ui/Tabs.tsx";
import { Tag, type TagTone } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";
import { candidateName } from "../lib/format.ts";
import {
  answerFieldLabel,
  answerStatusLabel,
  eventLabel,
  flagLabel,
  flagNeedsAttention,
  queueKindLabel,
  senderLabel,
  stage as copy,
  stageLabel,
  titleCase,
} from "../lib/labels.ts";
import { useEventStream } from "../lib/sse.ts";
import { proseState, proseStateCopy } from "../lib/summaryProse.ts";
import "../styles/surfaces/stage.css";
import { countryOfPhone, formatDateOnly, formatZoned, type PhoneCountry } from "./phone.ts";
import type { ApplicationLookup } from "./useApplicationLookup.ts";

/** The Application as the stage holds it. The Operator-only `cost` is dropped before any component sees it. */
export type StageDetail = Omit<ApplicationDetail, "cost">;

function dropCost({ cost, ...rest }: ApplicationDetail): StageDetail {
  void cost;
  return rest;
}

/** At most this many of the latest events are shown; the rest are counted. */
const EVENTS_SHOWN = 5;

const FIELDS = Object.keys(answerFieldLabel);

// A local copy of the Application page's formatter: the stage may not import a Recruiter page.
function formatAnswer(field: string, answers: Answers): string {
  const value = answers[field as keyof Answers];
  if (value === null || value === undefined) return copy.noValue;
  if (field === "zone" && typeof value === "object" && !Array.isArray(value)) return `${value.name}, ${value.city}`;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.map((v) => titleCase(String(v))).join(", ") : copy.noValue;
  if (typeof value === "string") return formatDateOnly(value);
  return String(value);
}

/** The words as the Candidate said them add nothing when they are the value again (case, accents and punctuation aside). */
function plain(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** The zone of the Candidate's conversation, the one the bubbles show. */
function zoneOf(detail: StageDetail): PhoneCountry {
  return detail.candidate.country ?? countryOfPhone(detail.candidate.phone ?? "") ?? "MX";
}

const STATUS_TONE: Record<AnswerStatus["status"], TagTone> = {
  valid: "success",
  pending: "neutral",
  invalid: "warn",
  flagged: "warn",
  knockout: "warn",
};

function stageBadge(stage: Stage, outcome: Outcome | null): StatusKind {
  if (outcome) return outcome;
  if (stage === "qualified" || stage === "approved" || stage === "interview_booked") return stage;
  return "screening";
}

// The row's "settle": a field whose value or status changes, and the stage badge when the stage or
// the outcome changes, play one short wash. Nothing plays for a refetch that changes nothing. The two
// parities name two copies of the same keyframes, so a second change on the same row plays again.
interface SettleState {
  prev: Readonly<Record<string, string>>;
  parity: Readonly<Record<string, 0 | 1>>;
  active: readonly string[];
}

function useSettle(signatures: Readonly<Record<string, string>>) {
  const [state, setState] = useState<SettleState>({ prev: signatures, parity: {}, active: [] });
  const changed = Object.keys(signatures).filter((key) => state.prev[key] !== signatures[key]);
  if (changed.length > 0) {
    const parity = { ...state.parity };
    for (const key of changed) parity[key] = parity[key] === 1 ? 0 : 1;
    setState({ prev: signatures, parity, active: [...new Set([...state.active, ...changed])] });
  }
  return {
    mark: (key: string): "a" | "b" | undefined => (state.active.includes(key) ? (state.parity[key] === 1 ? "a" : "b") : undefined),
    done: (key: string) => setState((current) => ({ ...current, active: current.active.filter((k) => k !== key) })),
  };
}

type Settle = ReturnType<typeof useSettle>;

function statusOf(detail: StageDetail, field: string): AnswerStatus | undefined {
  return detail.answer_status.find((a) => a.field === field);
}

function AnswerRows({ detail, settle, agent }: { detail: StageDetail; settle: Settle; agent: boolean }) {
  return (
    <ul className={agent ? "record-answers" : "record-answers record-answers--compact"}>
      {FIELDS.map((field) => {
        const entry = statusOf(detail, field);
        const status = entry?.status ?? "pending";
        const attempts = entry?.attempts ?? 0;
        const value = formatAnswer(field, detail.answers);
        const words = entry?.raw_input && plain(entry.raw_input) !== plain(value) ? entry.raw_input : null;
        return (
          <li
            key={field}
            className="record-row"
            data-field={field}
            data-settle={settle.mark(field)}
            onAnimationEnd={(event) => {
              if (event.target === event.currentTarget) settle.done(field);
            }}
          >
            <span className="record-row__label">{answerFieldLabel[field]}</span>
            <span className="record-row__main">
              <span className="record-row__value">{value}</span>
              {entry && status === "valid" ? (
                <span className="record-row__ok">
                  <Icon name="checkCircle" size="lg" label={answerStatusLabel.valid} />
                </span>
              ) : (
                <Tag tone={STATUS_TONE[status]}>{entry ? answerStatusLabel[status] : copy.pending}</Tag>
              )}
            </span>
            {agent && words ? <span className="record-row__extra">“{words}”</span> : null}
            {agent && attempts > 1 ? <span className="record-row__extra">{copy.attempts(attempts)}</span> : null}
            {entry?.note ? <span className="record-row__extra">{entry.note}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function Facts({ detail, country, now }: { detail: StageDetail; country: PhoneCountry; now?: Date | null }) {
  const { claim, control, interview, zone, locations } = detail;
  const holder = detail.control_user_name ?? senderLabel.human;
  const facts: [string, ReactNode][] = [
    [copy.claimLabel, claim ? `${claim.user_name} · ${formatZoned(claim.claimed_at, country, now)}` : copy.unclaimed],
    [copy.controlLabel, control === "human" ? `${holder} (${copy.takeover})` : senderLabel.agent],
    [
      copy.locationsLabel,
      locations.length
        ? locations.map((l) => (
            <span key={l.id} className="record-fact__line">
              {l.name}
            </span>
          ))
        : copy.noValue,
    ],
    [copy.zoneLabel, zone ? `${zone.name}, ${zone.city}` : copy.noValue],
    [copy.interviewLabel, interview ? `${interview.local_label} at ${interview.location_name}` : copy.noInterview],
  ];
  return (
    <dl className="record-facts" data-testid="record-facts">
      {facts.map(([label, value]) => (
        <div key={label} className="record-fact">
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function eventTitle(event: StageDetail["events"][number]): string {
  const title = eventLabel[event.kind];
  const to = event.kind === "stage_changed" ? event.data.to : undefined;
  if (typeof to !== "string") return title;
  return `${title} ${copy.stageChangedTo} ${stageLabel[to as Stage] ?? titleCase(to)}`;
}

function AgentView({ detail, settle, now }: { detail: StageDetail; settle: Settle; now?: Date | null }) {
  const country = zoneOf(detail);
  const events = [...detail.events].sort((a, b) => a.id - b.id);
  const shown = events.slice(-EVENTS_SHOWN);
  const earlier = events.length - shown.length;
  return (
    <>
      <Facts detail={detail} country={country} now={now} />
      <div className="record-split">
        <Card title={copy.answersTitle} level={3}>
          <AnswerRows detail={detail} settle={settle} agent />
        </Card>
        <Card title={copy.eventsTitle} level={3}>
          {earlier > 0 ? <p className="record-earlier">{copy.earlierEvents(earlier)}</p> : null}
          <ol className="record-events" aria-label={copy.eventsList}>
            {shown.map((event) => (
              <li key={event.id} className="record-event">
                <time className="record-event__time" dateTime={event.created_at}>
                  {formatZoned(event.created_at, country, now)}
                </time>
                <span className="record-event__kind">{eventTitle(event)}</span>
                <span className="record-event__actor">{event.actor === "user" ? (event.user_name ?? senderLabel.human) : senderLabel[event.actor]}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </>
  );
}

function RecruiterView({ detail, settle, now }: { detail: StageDetail; settle: Settle; now?: Date | null }) {
  const country = zoneOf(detail);
  const waiting = detail.open_queue_items.filter((item) => item.status === "open");
  // A different shape from the Agent's view: what waits for a person leads, in the live treatment (the One
  // Live Rule: lime means "this needs you"); the Summary follows at full width, and the answers are a
  // compact list with no words of the Candidate.
  return (
    <>
      {detail.flags.length > 0 && (
        <p className="record-flags">
          <span className="record-flags__label">{copy.flagsLabel}</span>
          {detail.flags.map((flag) => (
            <Tag key={flag} tone={flagNeedsAttention[flag] ? "warn" : "neutral"}>
              {flagLabel[flag]}
            </Tag>
          ))}
        </p>
      )}
      <Facts detail={detail} country={country} now={now} />
      {waiting.length > 0 && (
        <ul className="record-queue record-queue--lead" aria-label={copy.queueList}>
          {waiting.map((item) => (
            <li key={item.id} className="record-queue__item">
              <Tag tone="live">{queueKindLabel[item.kind]}</Tag>
              <span className="record-queue__text">
                {copy.waitingSince(queueKindLabel[item.kind], formatZoned(item.created_at, country, now))}
              </span>
            </li>
          ))}
        </ul>
      )}
      {detail.summary?.prose ? (
        <Card title={copy.summaryTitle} level={3}>
          <p className="record-summary">{detail.summary.prose}</p>
        </Card>
      ) : (
        <ProseStateCard summary={detail.summary} />
      )}
      <Card title={copy.answersTitle} level={3}>
        <AnswerRows detail={detail} settle={settle} agent={false} />
      </Card>
    </>
  );
}

/** The Summary card without its written part: says the backend is retrying it or has stopped. */
function ProseStateCard({ summary }: { summary: StageDetail["summary"] }) {
  const state = proseState(summary);
  if (state === null) return null;
  return (
    <Card title={copy.summaryTitle} level={3}>
      <p className="record-summary" role="status" data-prose-state={state}>
        {proseStateCopy[state]}
      </p>
    </Card>
  );
}

function RecordView({ detail, now, viewAs }: { detail: StageDetail; now?: Date | null; viewAs: ViewAs }) {
  const signatures: Record<string, string> = { stage: `${detail.stage}|${detail.outcome ?? ""}` };
  for (const field of FIELDS) {
    signatures[field] = `${formatAnswer(field, detail.answers)}|${statusOf(detail, field)?.status ?? ""}|${statusOf(detail, field)?.note ?? ""}`;
  }
  const settle = useSettle(signatures);
  return (
    <>
      <header className="record-head">
        <div className="record-who">
          <div className="record-title">
            <h2 className="record-name">{candidateName(detail.candidate)}</h2>
            <div
              className="record-stage"
              data-testid="record-stage"
              data-settle={settle.mark("stage")}
              onAnimationEnd={(event) => {
                if (event.target === event.currentTarget) settle.done("stage");
              }}
            >
              {detail.stage !== "qualified" && detail.stage !== "approved" && detail.stage !== "interview_booked" ? (
                <Tag>{stageLabel[detail.stage]}</Tag>
              ) : null}
              <StatusBadge status={stageBadge(detail.stage, detail.outcome)} />
            </div>
          </div>
          {detail.candidate.phone ? <p className="record-phone">{detail.candidate.phone}</p> : null}
        </div>
      </header>
      {viewAs === "recruiter" ? (
        // What a Recruiter reads: the Recruiter's view of the record, with no Agent's view to switch to.
        <div className="record-recruiter" data-testid="record-recruiter">
          <RecruiterView detail={detail} settle={settle} now={now} />
        </div>
      ) : (
        <Tabs
          label={copy.viewsLabel}
          tabs={[
            { value: "agent", label: copy.agentView, content: <AgentView detail={detail} settle={settle} now={now} /> },
            { value: "recruiter", label: copy.recruiterView, content: <RecruiterView detail={detail} settle={settle} now={now} /> },
          ]}
        />
      )}
    </>
  );
}

type ViewAs = "operator" | "recruiter";
const VIEW_AS_KEY = "criba.stage.viewAs";

/** The tab's remembered choice; Operator when nothing is stored or storage is unavailable. */
function readViewAs(): ViewAs {
  try {
    return sessionStorage.getItem(VIEW_AS_KEY) === "recruiter" ? "recruiter" : "operator";
  } catch {
    return "operator";
  }
}

function saveViewAs(value: ViewAs) {
  try {
    sessionStorage.setItem(VIEW_AS_KEY, value);
  } catch {
    // The switch works without storage; the choice is only not remembered.
  }
}

/**
 * The record's toolbar: the "View as" switch (a presentation choice of this screen: the server still sees the
 * Operator, ADR 0052, 0075) and, when the Application is Qualified with an open Approval, Approve. Approve is
 * the Review queue's call (`POST /api/queue/approvals/{id}`) as the logged-in Operator, so it is a named
 * person's decision (ADR 0012) through the same server path, quiet hours included (ADR 0038).
 */
function RecordToolbar({ detail, viewAs, onViewAs, onApproved }: { detail: StageDetail; viewAs: ViewAs; onViewAs: (v: ViewAs) => void; onApproved: () => void }) {
  const [done, setDone] = useState<string | null>(null);
  const approval =
    detail.status === "open" && detail.stage === "qualified"
      ? detail.open_queue_items.find((item) => item.kind === "approval" && item.status === "open")
      : undefined;
  const approve = useMutation({
    mutationFn: (itemId: string) => api.resolveApproval(itemId, { decision: "approved" }),
    onSuccess: () => {
      setDone(detail.id);
      onApproved();
    },
  });
  return (
    <div className="record-toolbar">
      <div className="record-viewas" role="group" aria-label={copy.viewAsLabel}>
        <span className="record-viewas__label" aria-hidden="true">
          {copy.viewAsLabel}
        </span>
        {(["operator", "recruiter"] as const).map((value) => (
          <Button
            key={value}
            variant={viewAs === value ? "primary" : "quiet"}
            aria-pressed={viewAs === value}
            onClick={() => onViewAs(value)}
          >
            {value === "operator" ? copy.viewAsOperator : copy.viewAsRecruiter}
          </Button>
        ))}
      </div>
      {approval && (
        <Button variant="primary" onClick={() => approve.mutate(approval.id)} loading={approve.isPending}>
          {copy.approveButton}
        </Button>
      )}
      {done === detail.id && (
        <p className="record-toolbar__status" role="status">
          {copy.approvedStatus}
        </p>
      )}
      {approve.isError && (
        <p className="record-toolbar__error" role="alert">
          {copy.approveFailed(approve.error.message)}
        </p>
      )}
    </div>
  );
}

/**
 * Under the record: for the Operator, two tabs, Agent activity first (each turn from stored records, with the run's
 * tokens and cost, owner decision 2026-10-12) and the full Jev readings panel (the Application page's Operator
 * panel, with no jump buttons and no economics); for the Recruiter, the Message labels and nothing scored (ADR 0075).
 * It reads the same detail as the record, so it refreshes with it; the panel's own reads key on the message count.
 *
 * Closed by default: a one-line strip with a toggle, so the record keeps the side column's height and a take's
 * record is fully visible at 1920 by 1080 without scrolling. Opened, the panel takes part of the column and
 * scrolls on its own; its content mounts (and fetches) only while open.
 */
function UnderRecord({ detail, viewAs }: { detail: StageDetail; viewAs: ViewAs }) {
  const panelId = useId();
  const bodyId = useId();
  const [open, setOpen] = useState(false);
  // The stage is Operator-only (StageGate); with no signed-in Operator known there is nothing to show here.
  const auth = useContext(AuthContext);
  if (auth?.role !== "operator") return null;
  return (
    <div
      className="stage-under"
      data-testid="stage-under"
      data-view-as={viewAs}
      data-open={open}
      role="region"
      aria-label={copy.underRecordRegion}
      // Focusable only while it can scroll (axe: a scrollable region is reachable by keyboard).
      tabIndex={open ? 0 : undefined}
    >
      <button type="button" className="stage-under__toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size="sm" />
        {viewAs === "operator" ? copy.underRecordToggleOperator : copy.messageLabelsTitle}
      </button>
      <div id={bodyId} className="stage-under__body" hidden={!open}>
        {open ? <UnderRecordBody detail={detail} viewAs={viewAs} panelId={panelId} /> : null}
      </div>
    </div>
  );
}

function UnderRecordBody({ detail, viewAs, panelId }: { detail: StageDetail; viewAs: ViewAs; panelId: string }) {
  return (
    <>
      {viewAs === "operator" ? (
        <Tabs
          label={copy.underRecordTabs}
          tabs={[
            { value: "activity", label: copy.activityTab, content: <AgentActivity detail={detail} country={zoneOf(detail)} /> },
            { value: "jev", label: copy.jevReadingsTab, content: <OperatorPanel id={panelId} detail={detail} economics={false} /> },
          ]}
        />
      ) : (
        <Card title={copy.messageLabelsTitle} level={3}>
          <MessageLabelList labels={detail.message_labels ?? []} timeOf={(iso) => formatZoned(iso, zoneOf(detail))} />
        </Card>
      )}
    </>
  );
}

/**
 * A still outline of the record that will fill in: the five facts and the eight answers, each with a dash.
 * Nothing is loading yet, so nothing moves and nothing says "loading". Decoration only.
 */
function Ghost() {
  const facts = [copy.claimLabel, copy.controlLabel, copy.locationsLabel, copy.zoneLabel, copy.interviewLabel];
  return (
    <div className="record-ghost" aria-hidden="true">
      <span className="record-ghost__title" />
      <dl className="record-ghost__facts">
        {facts.map((label) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{copy.ghostValue}</dd>
          </div>
        ))}
      </dl>
      <ul className="record-ghost__rows">
        {FIELDS.map((field) => (
          <li key={field}>
            <span>{answerFieldLabel[field]}</span>
            <span>{copy.ghostValue}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The live Application of this Take, beside the phone. `lookup` finds it by exact phone; this pane
 * reads its detail (with `cost` dropped in `select`), refetches it on every stream event and every
 * open, and shows it in two views: the Agent's (answers with the Candidate's own words, events) and
 * the Recruiter's (the detail as a Recruiter reads it). Its one action is Approve (see RecordToolbar). `now` is the sim
 * clock: an event on its day shows HH:mm only.
 */
export function RecordPane({ lookup, now }: { lookup: ApplicationLookup; now?: Date | null }) {
  const id = lookup.application?.id ?? null;
  const client = useQueryClient();
  const [viewAs, setViewAs] = useState<ViewAs>(readViewAs);
  const key = ["stage-detail", id];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.getApplication(id as string),
    enabled: id !== null,
    retry: false,
    select: dropCost,
  });

  // The stream starts at the newest row, so every open refetches too, to cover the gap.
  // The sim clock is read again with it, so the strip shows the minute the new row was written in.
  const refetch = () => {
    void client.invalidateQueries({ queryKey: key });
    void client.invalidateQueries({ queryKey: ["sim-clock"] });
  };
  useEventStream(id ? api.applicationStreamUrl(id) : null, ["message", "control", "event"], refetch, refetch);

  let body: ReactNode;
  if (!lookup.application) {
    if (lookup.isError) {
      body = <ErrorState message={copy.lookupError} onRetry={lookup.retry} />;
    } else if (!lookup.started) {
      body = (
        <div className="record-placeholder">
          <div className="record-placeholder__message">
            <Icon name="person" size="lg" />
            <p>{copy.recordEmpty}</p>
          </div>
          <Ghost />
        </div>
      );
    } else {
      body = (
        <div className="record-placeholder">
          <Skeleton shape="block" label={copy.recordWaiting} />
          <p>{copy.recordWaitingHint}</p>
          <Ghost />
        </div>
      );
    }
  } else if (query.data) {
    body = (
      <>
        <RecordToolbar
          detail={query.data}
          viewAs={viewAs}
          onViewAs={(value) => {
            saveViewAs(value);
            setViewAs(value);
          }}
          onApproved={refetch}
        />
        <RecordView key={`${lookup.application.id}:${viewAs}`} detail={query.data} now={now} viewAs={viewAs} />
      </>
    );
  } else if (query.isError) {
    body = <ErrorState message={copy.detailError} onRetry={() => void query.refetch()} />;
  } else {
    body = <Skeleton shape="block" label={copy.recordLoading} />;
  }

  return (
    <div className="stage-record-stack">
      <section className="stage-record record" aria-label={copy.recordRegion}>
        {body}
      </section>
      {lookup.application && query.data ? <UnderRecord detail={query.data} viewAs={viewAs} /> : null}
    </div>
  );
}
