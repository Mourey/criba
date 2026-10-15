import { keepPreviousData, useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link } from "react-router";
import { api } from "../api/client.ts";
import type { ApplicationSummary, Location, Outcome, QueueItem, Slot, Stage } from "../api/types.ts";
import { useAuth } from "../auth/context.ts";
import { FlagTags, StageOutcome } from "../components/Flags.tsx";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";
import { candidateName, formatDateTime } from "../lib/format.ts";
import { today as copy } from "../lib/labels.ts";
import { useEventStream } from "../lib/sse.ts";
import { utcToLocalParts } from "../lib/tz.ts";
import { useSimClock } from "../lib/useSimClock.ts";
import "../styles/surfaces/today.css";

const APPROVALS_LIMIT = 25;
const TAKEOVERS_LIMIT = 25;
const QUALIFIED_LIMIT = 10;
const HOUR_MS = 3_600_000;
// A three-day window around now (36 hours either side), so every Location's "today" falls inside it.
const WINDOW_MS = 36 * HOUR_MS;

// Each key starts with the key the actions already invalidate (ReviewQueue and the detail page).
const QUEUE_KEY = ["queue", "today"] as const;
const TAKEOVERS_KEY = ["applications", "today", "takeovers"] as const;
const QUALIFIED_KEY = ["applications", "today", "qualified"] as const;

/** A whole-second UTC ISO string. */
const isoSeconds = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");

/** The window of Slots to ask for: "now" floored to the hour, 36 hours either side. */
function slotWindow(now: Date): { from: string; to: string } {
  const hour = Math.floor(now.getTime() / HOUR_MS) * HOUR_MS;
  return { from: isoSeconds(hour - WINDOW_MS), to: isoSeconds(hour + WINDOW_MS) };
}

/**
 * A booked Slot is "today" on the calendar day of its own Location, never the UTC date or the browser's.
 * A Slot with no booking, an unknown Location or a timezone the browser rejects is left out.
 */
function isToday(slot: Slot, timeZones: ReadonlyMap<string, string>, nowIso: string): boolean {
  const timeZone = timeZones.get(slot.location_id);
  if (!timeZone || !slot.booking) return false;
  try {
    return utcToLocalParts(slot.starts_at, timeZone).date === utcToLocalParts(nowIso, timeZone).date;
  } catch {
    return false;
  }
}

/**
 * The first Location and "+N" for the rest. The full list stays available to assistive technology (hidden
 * text) and to a pointer (title), so the row stays short without losing what covers the Application.
 */
function Locations({ app }: { app: ApplicationSummary }) {
  const names = app.locations.map((l) => l.name);
  if (names.length <= 1) return <span className="today-sub">{names[0] ?? ""}</span>;
  const full = names.join(", ");
  return (
    <span className="today-sub today-loc" title={full}>
      <span className="ui-sr-only">{full}</span>
      <span aria-hidden="true" className="today-loc__text">
        <span className="today-loc__name">{names[0]}</span>
        <span className="today-loc__more">{`+${names.length - 1}`}</span>
      </span>
    </span>
  );
}

export function Today() {
  const queryClient = useQueryClient();
  const refresh = () => {
    for (const queryKey of [QUEUE_KEY, TAKEOVERS_KEY, QUALIFIED_KEY]) void queryClient.invalidateQueries({ queryKey });
  };
  // Each frame, and each (re)open, refreshes the three stream-driven sections. Slots are not stream-driven.
  useEventStream(api.queueStreamUrl(), ["queue"], refresh, refresh);

  return (
    <div className="today">
      <h1>{copy.heading}</h1>
      <div className="today-grid">
        <div className="today-col">
          <ApprovalsSection />
        </div>
        <div className="today-col">
          <TakeoversSection />
          <InterviewsSection />
          <QualifiedSection />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// Section: a labelled region whose h2 can take focus (Card's title cannot), with the polite status
// element beside it so an announcement survives the removal of the row that caused it.

interface SectionProps {
  title: string;
  count?: number;
  actions?: ReactNode;
  headingRef?: RefObject<HTMLHeadingElement | null>;
  /** The polite status text. Passing a string (even an empty one) renders the live region. */
  status?: string;
  children: ReactNode;
}

function Section({ title, count, actions, headingRef, status, children }: SectionProps) {
  const headingId = useId();
  return (
    <section className="ui-card ui-card--flush" aria-labelledby={headingId}>
      <header className="ui-card__head today-section__head">
        <div className="today-section__title">
          <h2 id={headingId} ref={headingRef} tabIndex={-1} className="ui-card__title today-section__heading">
            {title}
          </h2>
          {count !== undefined ? <span className="ui-card__count">{count}</span> : null}
        </div>
        {actions ? <div className="ui-card__actions">{actions}</div> : null}
      </header>
      {status !== undefined ? (
        <p className="today-status" role="status" aria-live="polite">
          {status}
        </p>
      ) : null}
      <div className="ui-card__body">{children}</div>
    </section>
  );
}

interface StateProps {
  /** No answer yet. */
  pending: boolean;
  /** The failure, only while there is nothing to show. */
  failure: Error | null;
  onRetry: () => void;
  empty: boolean;
  emptyText: string;
  children: ReactNode;
}

/** Skeleton on first load, ErrorState with Retry on failure, the empty sentence, or the rows. */
function SectionBody({ pending, failure, onRetry, empty, emptyText, children }: StateProps) {
  if (pending) {
    return (
      <div className="today-state">
        <Skeleton shape="block" />
      </div>
    );
  }
  if (failure) {
    return (
      <div className="today-state">
        <ErrorState message={failure.message} onRetry={onRetry} />
      </div>
    );
  }
  if (empty) return <p className="today-empty">{emptyText}</p>;
  return <>{children}</>;
}

/** The view of one query: rows stay on screen through any background refetch or failed refetch. */
function viewOf<T>(query: UseQueryResult<T, Error>) {
  const hasData = query.data !== undefined;
  return {
    pending: !hasData && !query.isError,
    failure: !hasData && query.isError ? query.error : null,
    onRetry: () => void query.refetch(),
  };
}

// ---------------------------------------------------------------------------------------------------
// Stage with a one-time emphasis: the wrapper carries data-emphasis only after the Stage changed on a
// refetch (never on first render), and the key restarts the CSS animation once per change.

function StageMark({ stage, outcome }: { stage: Stage; outcome: Outcome | null }) {
  const [seen, setSeen] = useState(stage);
  const [changes, setChanges] = useState(0);
  if (seen !== stage) {
    setSeen(stage);
    setChanges(changes + 1);
  }
  const marked = changes > 0;
  return (
    <span key={changes} className={marked ? "today-emphasis" : undefined} data-emphasis={marked ? "true" : undefined}>
      <StageOutcome stage={stage} outcome={outcome} />
    </span>
  );
}

// ---------------------------------------------------------------------------------------------------
// Approvals waiting

function ApprovalsSection() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [status, setStatus] = useState("");
  const query = useQuery({
    queryKey: QUEUE_KEY,
    queryFn: () => api.listQueue({ kind: ["approval"], status: "open", limit: APPROVALS_LIMIT }),
  });
  const items = query.data?.items ?? [];

  const onApproved = (name: string) => {
    setStatus(copy.approvedStatus(name));
    headingRef.current?.focus();
  };

  const columns: TableColumn<QueueItem>[] = [
    {
      key: "candidate",
      header: copy.candidateHeader,
      render: (item) => (
        <>
          <Link id={approvalLinkId(item)} to={`/applications/${item.application.id}`} className="today-link">
            {candidateName(item.application.candidate)}
          </Link>
          <Locations app={item.application} />
        </>
      ),
    },
    {
      key: "stage",
      header: copy.stageHeader,
      render: (item) => (
        <span className="today-cell-stack">
          <StageMark stage={item.application.stage} outcome={item.application.outcome} />
          {item.application.flags.length > 0 ? <FlagTags flags={item.application.flags} /> : null}
        </span>
      ),
    },
    { key: "waiting", header: copy.waitingSince, mono: true, render: (item) => formatDateTime(item.created_at) },
    {
      key: "action",
      header: copy.actionHeader,
      align: "end",
      render: (item) => <ApproveCell item={item} onApproved={onApproved} />,
    },
  ];

  return (
    <Section
      title={copy.approvalsTitle}
      count={query.data ? items.length : undefined}
      actions={<Link to="/queue">{copy.reviewQueueLink}</Link>}
      headingRef={headingRef}
      status={status}
    >
      <SectionBody {...viewOf(query)} empty={items.length === 0} emptyText={copy.approvalsEmpty}>
        <Table
          caption={copy.approvalsCaption}
          hideCaption
          columns={columns}
          rows={items}
          rowKey={(item) => item.id}
          empty={copy.approvalsEmpty}
        />
        {query.data?.next_cursor ? <p className="today-more">{copy.moreInQueue(APPROVALS_LIMIT)}</p> : null}
      </SectionBody>
    </Section>
  );
}

const approvalLinkId = (item: QueueItem) => `today-approval-${item.id}`;

/** Approve with the call the Review queue makes. The row stays until the server says it is gone. */
function ApproveCell({ item, onApproved }: { item: QueueItem; onApproved: (name: string) => void }) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const app = item.application;
  const name = candidateName(app.candidate);

  const approve = useMutation({
    mutationFn: () => api.resolveApproval(item.id, { decision: "approved" }),
    onSuccess: () => {
      setMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      void queryClient.invalidateQueries({ queryKey: ["application", app.id] });
      onApproved(name);
    },
    onError: (e) => {
      setMessage(e instanceof Error ? e.message : copy.approveFailed);
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  return (
    <div className="today-action">
      <Button
        variant="primary"
        loading={approve.isPending}
        aria-describedby={approvalLinkId(item)}
        onClick={() => {
          if (approve.isPending) return;
          setMessage(null);
          approve.mutate();
        }}
      >
        {copy.approve}
      </Button>
      {message ? (
        <p className="today-row-error" role="alert">
          {message}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// Takeovers waiting on me

function TakeoversSection() {
  const { me } = useAuth();
  const meId = me?.user.id;
  const query = useQuery({
    queryKey: TAKEOVERS_KEY,
    queryFn: () =>
      api.listApplications({ status: "open", flag: ["takeover"], claimed_by: "me", limit: TAKEOVERS_LIMIT }),
  });
  const items = query.data?.items ?? [];

  const holder = (app: ApplicationSummary) =>
    meId !== undefined && app.control_user_id === meId ? copy.you : (app.control_user_name ?? copy.someoneElse);

  const columns: TableColumn<ApplicationSummary>[] = [
    { key: "candidate", header: copy.candidateHeader, render: applicationCell },
    {
      key: "holder",
      header: copy.heldByHeader,
      render: (app) => (
        <span className="today-cell-stack">
          <span>{holder(app)}</span>
          {app.flags.length > 0 ? <FlagTags flags={app.flags} /> : null}
        </span>
      ),
    },
    { key: "updated", header: copy.updatedHeader, mono: true, render: (app) => formatDateTime(app.updated_at) },
  ];

  return (
    <Section title={copy.takeoversTitle} count={query.data ? items.length : undefined}>
      <SectionBody {...viewOf(query)} empty={items.length === 0} emptyText={copy.takeoversEmpty}>
        <Table
          caption={copy.takeoversCaption}
          hideCaption
          columns={columns}
          rows={items}
          rowKey={(app) => app.id}
          empty={copy.takeoversEmpty}
        />
      </SectionBody>
    </Section>
  );
}

/** The Candidate's name as a link to the Application, with the Locations under it. */
function applicationCell(app: ApplicationSummary) {
  return (
    <>
      <Link to={`/applications/${app.id}`} className="today-link">
        {candidateName(app.candidate)}
      </Link>
      <Locations app={app} />
    </>
  );
}

// ---------------------------------------------------------------------------------------------------
// Interviews today

interface InterviewRow {
  slot: Slot;
  time: string;
}

function InterviewsSection() {
  const clock = useSimClock();
  // The sim clock when it answers, otherwise the browser's. The Slots query waits until the probe has
  // settled, so there is never a first request on browser time followed by a second on sim time.
  const now = clock.available && clock.now ? clock.now : new Date();
  const { from, to } = slotWindow(now);

  const slots = useQuery({
    queryKey: ["slots", "today", from, to],
    queryFn: () => api.listSlots({ status: "booked", from, to }),
    enabled: !clock.isLoading,
    placeholderData: keepPreviousData,
  });
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.listLocations() });

  const timeZones = new Map((locations.data?.items ?? []).map((l: Location) => [l.id, l.timezone] as const));
  const nowIso = now.toISOString();
  const rows: InterviewRow[] = (slots.data?.items ?? [])
    .filter((slot) => isToday(slot, timeZones, nowIso))
    .map((slot) => ({
      slot,
      time: slot.local_label ?? utcToLocalParts(slot.starts_at, timeZones.get(slot.location_id)!).time,
    }));

  const slotsView = viewOf(slots);
  const locationsView = viewOf(locations);
  const pending = clock.isLoading || slotsView.pending || locationsView.pending;
  const failure = slotsView.failure ?? locationsView.failure;
  const onRetry = () => {
    if (slotsView.failure) slotsView.onRetry();
    if (locationsView.failure) locationsView.onRetry();
  };

  const columns: TableColumn<InterviewRow>[] = [
    { key: "time", header: copy.timeHeader, mono: true, render: (row) => row.time },
    { key: "location", header: copy.locationHeader, render: (row) => row.slot.location_name ?? "" },
    {
      key: "candidate",
      header: copy.candidateHeader,
      render: (row) => (
        <Link to={`/applications/${row.slot.booking?.application_id ?? ""}`} className="today-link">
          {row.slot.booking?.candidate_name ?? copy.unnamedCandidate}
        </Link>
      ),
    },
  ];

  return (
    <Section title={copy.interviewsTitle} count={pending || failure ? undefined : rows.length}>
      <SectionBody
        pending={pending && !failure}
        failure={failure}
        onRetry={onRetry}
        empty={rows.length === 0}
        emptyText={copy.interviewsEmpty}
      >
        <Table
          caption={copy.interviewsCaption}
          hideCaption
          columns={columns}
          rows={rows}
          rowKey={(row) => row.slot.id}
          empty={copy.interviewsEmpty}
        />
      </SectionBody>
    </Section>
  );
}

// ---------------------------------------------------------------------------------------------------
// Recently Qualified

function QualifiedSection() {
  const query = useQuery({
    queryKey: QUALIFIED_KEY,
    queryFn: () =>
      api.listApplications({ status: "open", stage: ["qualified"], sort: "-updated_at", limit: QUALIFIED_LIMIT }),
  });
  const items = query.data?.items ?? [];

  const columns: TableColumn<ApplicationSummary>[] = [
    { key: "candidate", header: copy.candidateHeader, render: applicationCell },
    {
      key: "flags",
      header: copy.flagsHeader,
      render: (app) => (app.flags.length > 0 ? <FlagTags flags={app.flags} /> : <span className="today-sub">-</span>),
    },
    { key: "updated", header: copy.updatedHeader, mono: true, render: (app) => formatDateTime(app.updated_at) },
  ];

  return (
    <Section
      title={copy.qualifiedTitle}
      count={query.data ? items.length : undefined}
      actions={<Link to="/applications?stage=qualified">{copy.allApplicationsLink}</Link>}
    >
      <SectionBody {...viewOf(query)} empty={items.length === 0} emptyText={copy.qualifiedEmpty}>
        <Table
          caption={copy.qualifiedCaption}
          hideCaption
          columns={columns}
          rows={items}
          rowKey={(app) => app.id}
          empty={copy.qualifiedEmpty}
        />
      </SectionBody>
    </Section>
  );
}
