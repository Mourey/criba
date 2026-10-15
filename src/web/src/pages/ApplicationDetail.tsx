import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError } from "../api/client.ts";
import type {
  Answers,
  ApplicationDetail as Detail,
  BookingLink,
  Message,
  Outcome,
  Stage,
  Summary,
  TakeoverState,
} from "../api/types.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { FlagTags, StageOutcome } from "../components/Flags.tsx";
import { OperatorPanel } from "../components/OperatorPanel.tsx";
import { RejectionForm } from "../components/RejectionForm.tsx";
import { TakeoverPanel } from "../components/TakeoverPanel.tsx";
import { Transcript } from "../components/Transcript.tsx";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import { Tag } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";
import { appendEvent, appendMessage, applyControl } from "../lib/applicationCache.ts";
import {
  candidateName,
  formatDate,
  formatDateTime,
  formatDuration,
} from "../lib/format.ts";
import {
  SCREENING_STAGES,
  answerFieldLabel,
  answerStatusLabel,
  countryLabel,
  eventLabel,
  outcomeLabel,
  queueKindLabel,
  rejectionReasonLabel,
  senderLabel,
  stageLabel,
  today as copy,
  titleCase,
} from "../lib/labels.ts";
import { panelTitle } from "../lib/labels/operator.ts";
import { useEventStream } from "../lib/sse.ts";
import { proseState, proseStateCopy } from "../lib/summaryProse.ts";
import "../styles/surfaces/today.css";

function formatAnswer(field: string, answers: Answers): string {
  const value = answers[field as keyof Answers];
  if (value === null || value === undefined) return "-";
  if (field === "zone" && typeof value === "object" && !Array.isArray(value)) return `${value.name}, ${value.city}`;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.map((v) => titleCase(String(v))).join(", ") : "-";
  return String(value);
}

/**
 * The Stage and Outcome badges, with a one-time emphasis: the wrapper carries data-emphasis only after the
 * Stage changed on a refetch (never on first render), and the key restarts the CSS animation once per change.
 */
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

/** The Stage change or the field an entry carries, in canonical labels. Only codes the payload already holds. */
function eventDetail(e: Detail["events"][number]): string | null {
  const data = e.data as Record<string, unknown>;
  if (e.kind === "stage_changed") {
    const from = typeof data.from === "string" ? stageLabel[data.from as Stage] : undefined;
    const to = typeof data.to === "string" ? stageLabel[data.to as Stage] : undefined;
    return from && to ? `${from} → ${to}` : null;
  }
  if (e.kind === "answer_recorded" && typeof data.field === "string") return answerFieldLabel[data.field] ?? null;
  return null;
}

/** Who an event is from, in plain words. */
function actorOf(e: Detail["events"][number]): string {
  return e.actor === "user" ? (e.user_name ?? senderLabel.human) : (senderLabel[e.actor] ?? e.actor);
}

/** The Timeline shows this many of the latest events until "Show all" is pressed. */
const TIMELINE_WINDOW = 10;

interface AnswerRow {
  field: string;
}

interface StepRow {
  label: ReactNode;
  time: string;
}

export function ApplicationDetail() {
  const { id = "" } = useParams();
  const { me, role } = useAuth();
  const queryClient = useQueryClient();
  const key = ["application", id];
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [link, setLink] = useState<BookingLink | null>(null);
  const actionsRef = useRef<HTMLElement>(null);
  // Keyed by Application, so the line never shows under another Candidate when the route changes.
  const [approved, setApproved] = useState<{ id: string; name: string } | null>(null);
  const [allEvents, setAllEvents] = useState(false);
  const timelineId = useId();
  // Open on every page load and not remembered: the cost it holds is expected on first load.
  const [panelOpen, setPanelOpen] = useState(true);
  const panelId = useId();
  const [selectedMessage, setSelectedMessage] = useState<number | null>(null);

  const query = useQuery({
    queryKey: key,
    queryFn: () => api.getApplication(id),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });

  // Live relay: new messages, control changes and timeline entries for this Application.
  // The stream starts at the newest row, so each (re)connect refetches to cover the gap.
  useEventStream(
    id ? api.applicationStreamUrl(id) : null,
    ["message", "control", "event"],
    (event) => {
      const update = (fn: (app: Detail) => Detail) =>
        queryClient.setQueryData<Detail>(key, (old) => (old ? fn(old) : old));
      if (event.type === "message") update((app) => appendMessage(app, event.data as unknown as Message));
      else if (event.type === "control") {
        update((app) => applyControl(app, event.data as unknown as TakeoverState));
        void queryClient.invalidateQueries({ queryKey: key });
      } else if (event.type === "event") {
        update((app) => appendEvent(app, event.data as unknown as Detail["events"][number]));
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
    () => void queryClient.invalidateQueries({ queryKey: key }),
  );

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
    void queryClient.invalidateQueries({ queryKey: ["applications"] });
    void queryClient.invalidateQueries({ queryKey: ["queue"] });
  };
  const onError = (e: unknown) => setActionError(e instanceof Error ? e.message : "Something went wrong.");
  const onDone = () => {
    setActionError(null);
    refresh();
  };

  const claim = useMutation({
    mutationFn: () => api.claim(id),
    onSuccess: onDone,
    onError,
  });
  const release = useMutation({
    mutationFn: () => api.releaseClaim(id),
    onSuccess: onDone,
    onError,
  });
  const takeOverClaim = useMutation({
    mutationFn: () => api.takeOverIdleClaim(id),
    onSuccess: onDone,
    onError,
  });
  const reject = useMutation({
    mutationFn: (v: { reason: Parameters<typeof api.reject>[1]["reason"]; note: string | null }) => api.reject(id, v),
    onSuccess: () => {
      setRejecting(false);
      onDone();
    },
    onError,
  });
  // The same call as the Review queue and the Today row: one request, the three keys of refresh().
  const approve = useMutation({
    mutationFn: (itemId: string) => api.resolveApproval(itemId, { decision: "approved" }),
    onSuccess: () => {
      onDone();
      setApproved({
        id,
        name: candidateName(query.data?.candidate ?? { full_name: null }),
      });
      actionsRef.current?.focus();
    },
    onError,
  });
  const bookingLink = useMutation({
    mutationFn: () => api.createBookingLink(id),
    onSuccess: (l) => {
      setActionError(null);
      setLink(l);
      refresh();
    },
    onError,
  });

  if (query.isPending) {
    return (
      <div className="detail-state">
        <Skeleton shape="block" />
      </div>
    );
  }
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    if (notFound) {
      return (
        <div className="card">
          <h1>Application not found</h1>
          <p>It does not exist, or it is outside your Locations.</p>
          <Link to="/applications">Back to Applications</Link>
        </div>
      );
    }
    return (
      <div className="detail-state stack">
        <h1>Could not load the Application</h1>
        <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />
        <Link to="/applications">Back to Applications</Link>
      </div>
    );
  }

  const app = query.data;
  const closed = app.status === "closed";
  const iHoldClaim = app.claim?.user_id === me?.user.id;
  // The Operator panel follows the effective role, so "Preview as Recruiter" hides it.
  const showPanel = canSeeEconomics(role);
  // Approve shows only on an open Application with an open approval. It is then the one primary action.
  const approval = closed ? undefined : app.open_queue_items.find((q) => q.kind === "approval" && q.status === "open");
  const claimVariant = approval ? "secondary" : "primary";
  const where = `${app.locations.map((l) => l.name).join(", ") || "No Location"}${app.zone ? ` · Zone: ${app.zone.name}, ${app.zone.city}` : ""}`;

  const answerColumns: TableColumn<AnswerRow>[] = [
    { key: "field", header: "Field", render: (r) => answerFieldLabel[r.field] },
    {
      key: "answer",
      header: "Answer",
      render: (r) => formatAnswer(r.field, app.answers),
    },
    {
      key: "validation",
      header: "Validation",
      render: (r) => {
        const status = app.answer_status.find((s) => s.field === r.field);
        return (
          <>
            <Tag tone={status && ["invalid", "flagged", "knockout"].includes(status.status) ? "warn" : "neutral"}>
              {answerStatusLabel[status?.status ?? "pending"]}
            </Tag>
            {status && status.attempts !== undefined && status.attempts > 1 && (
              <span className="detail-muted"> {status.attempts} attempts</span>
            )}
            {status?.note && <div className="detail-muted">{status.note}</div>}
          </>
        );
      },
    },
    {
      key: "words",
      header: "Candidate's words",
      render: (r) => {
        const words = app.answer_status.find((s) => s.field === r.field)?.raw_input ?? null;
        return (
          <span className="detail-muted detail-words" title={words ?? undefined}>
            {words ?? "-"}
          </span>
        );
      },
    },
  ];
  const answerRows: AnswerRow[] = Object.keys(answerFieldLabel).map((field) => ({ field }));

  const stepColumns: TableColumn<StepRow>[] = [
    { key: "step", header: "Step", render: (r) => r.label },
    {
      key: "time",
      header: "Time",
      mono: true,
      align: "end",
      render: (r) => r.time,
    },
  ];
  const stepRows: StepRow[] = [
    ...SCREENING_STAGES.filter((s) => app.stage_durations_seconds?.[s] !== undefined).map((s) => ({
      label: stageLabel[s],
      time: formatDuration(app.stage_durations_seconds?.[s]),
    })),
    {
      label: <b>Elapsed since start</b>,
      time: formatDuration(app.total_seconds),
    },
  ];

  return (
    <div className="stack detail-page">
      <div className="detail-strip">
        <div className="page-head">
          <div>
            <p className="detail-muted">
              <Link to="/applications">Applications</Link> / <span title={app.id}>{candidateName(app.candidate)}</span>
            </p>
            <h1>{candidateName(app.candidate)}</h1>
            <p className="detail-muted">
              {app.candidate.phone ?? "No phone"} ·{" "}
              {app.candidate.country ? countryLabel[app.candidate.country] : "Country unknown"} ·{" "}
              {app.candidate.language ?? "language unknown"} · {app.channel}
            </p>
          </div>
          <div className="row">
            <StageMark stage={app.stage} outcome={app.outcome} />
            {app.flags.length > 0 && <FlagTags flags={app.flags} />}
            <span className="detail-muted">In stage {formatDuration(app.time_in_stage_seconds)}</span>
          </div>
        </div>

        {actionError && (
          <p className="notice error" role="alert">
            {actionError}
          </p>
        )}

        <section ref={actionsRef} className="ui-card ui-card--pad detail-actions" aria-label="Actions" tabIndex={-1}>
          <div className="detail-actions__bar row spread">
            <div className="detail-actions__claim">
              <b>Claim:</b>{" "}
              {app.claim ? (
                <>
                  {app.claim.user_name} since {formatDateTime(app.claim.claimed_at)}
                  {app.claim.idle && <Tag tone="warn">Claimed, idle</Tag>}
                </>
              ) : (
                <span className="detail-muted">Unclaimed</span>
              )}
              <div className="detail-muted detail-actions__where" title={where}>
                {app.locations.map((l) => l.name).join(", ") || "No Location"}
                {app.zone ? ` · Zone: ${app.zone.name}, ${app.zone.city}` : ""}
              </div>
            </div>
            <div className="detail-actions__bar">
              <div className="detail-actions__group">
                {approval && (
                  <Button variant="primary" onClick={() => approve.mutate(approval.id)} loading={approve.isPending}>
                    Approve
                  </Button>
                )}
                {!closed && !app.claim && (
                  <Button variant={claimVariant} onClick={() => claim.mutate()} loading={claim.isPending}>
                    Claim
                  </Button>
                )}
                {!closed && app.claim && iHoldClaim && (
                  <Button variant="secondary" onClick={() => release.mutate()} loading={release.isPending}>
                    Release Claim
                  </Button>
                )}
                {!closed && app.claim && !iHoldClaim && app.claim.idle && (
                  <Button variant={claimVariant} onClick={() => takeOverClaim.mutate()} loading={takeOverClaim.isPending}>
                    Take over Claim
                  </Button>
                )}
                {!closed && app.stage === "approved" && (
                  <Button variant="secondary" onClick={() => bookingLink.mutate()} loading={bookingLink.isPending}>
                    Create booking link
                  </Button>
                )}
              </div>
              <div className="detail-actions__group">
                {!closed && (
                  <Button variant="danger" onClick={() => setRejecting((v) => !v)} aria-expanded={rejecting}>
                    Reject...
                  </Button>
                )}
                <a className="ui-btn ui-btn--secondary" href={api.exportUrl(app.id)} download>
                  Export JSON
                </a>
              </div>
            </div>
          </div>
          <p className="detail-actions__status" role="status" aria-live="polite">
            {approved?.id === id ? copy.approvedStatus(approved.name) : ""}
          </p>
          {link && (
            <p className="notice detail-block">
              Booking link (expires {formatDateTime(link.expires_at)}): <a href={link.url}>{link.url}</a>
            </p>
          )}
          {rejecting && (
            <div className="detail-block detail-block--wide">
              <RejectionForm
                busy={reject.isPending}
                onSubmit={(reason, note) => reject.mutate({ reason, note })}
                onCancel={() => setRejecting(false)}
              />
            </div>
          )}
          {closed && (
            <p className="detail-muted detail-block">
              Closed {formatDateTime(app.closed_at)}
              {app.outcome ? `: ${outcomeLabel[app.outcome]}` : ""}
              {app.knockout_criterion ? ` (${app.knockout_criterion === "zone" ? "Zone" : "driver's license"})` : ""}
              {app.rejection_reason ? `, ${rejectionReasonLabel[app.rejection_reason]}` : ""}.
            </p>
          )}
        </section>

        {showPanel && (
          <Button
            variant="secondary"
            className="detail-strip__toggle"
            aria-expanded={panelOpen}
            aria-controls={panelId}
            onClick={() => setPanelOpen((v) => !v)}
          >
            {panelTitle}
          </Button>
        )}
      </div>

      <div className={showPanel && panelOpen ? "detail-columns detail-columns--panel" : "detail-columns"}>
        <div className="detail-col" role="group" aria-label="Application details" tabIndex={0}>
          <Card title="Summary">
            {app.summary ? (
              <>
                {app.summary.prose && <p>{app.summary.prose}</p>}
                <ProseStateNote summary={app.summary} />
                {/* The answers themselves are in the Answers card below. */}
                <dl className="stack detail-dl">
                  <div className="row spread">
                    <dt className="detail-muted">Stage and Outcome</dt>
                    <dd className="detail-dd">
                      <StageOutcome stage={app.summary.structured.stage} outcome={app.summary.structured.outcome} />
                    </dd>
                  </div>
                  <div className="row spread">
                    <dt className="detail-muted">Language</dt>
                    <dd className="detail-dd">{app.summary.structured.language}</dd>
                  </div>
                  <div className="row spread">
                    <dt className="detail-muted">Time in Screening at summary</dt>
                    <dd className="detail-dd">{formatDuration(app.summary.structured.durations_seconds.total)}</dd>
                  </div>
                  <div className="row spread">
                    <dt className="detail-muted">Nudges</dt>
                    <dd className="detail-dd">{app.summary.structured.nudges}</dd>
                  </div>
                  <div className="row spread">
                    <dt className="detail-muted">Escalations</dt>
                    <dd className="detail-dd">{app.summary.structured.escalations}</dd>
                  </div>
                  <div className="row spread">
                    <dt className="detail-muted">Flags</dt>
                    <dd className="detail-dd">
                      <FlagTags flags={app.summary.structured.flags} />
                    </dd>
                  </div>
                </dl>
                <p className="detail-muted">Written {formatDateTime(app.summary.written_at)}</p>
              </>
            ) : (
              <p className="detail-muted">No summary yet. It is written when Screening finishes.</p>
            )}
          </Card>

          <Card title="Answers" flush>
            <Table
              caption="Answers by field"
              hideCaption
              columns={answerColumns}
              rows={answerRows}
              rowKey={(r) => r.field}
              empty="No answers yet."
            />
          </Card>

          {app.interview && (
            <Card title="Interview">
              <p>
                {app.interview.local_label} at {app.interview.location_name}
                {app.interview.address ? `, ${app.interview.address}` : ""}
              </p>
              <a className="ui-btn ui-btn--secondary" href={api.inviteUrl(app.id)} download>
                Download invite (.ics)
              </a>
            </Card>
          )}

          <Card title="Time per step" flush>
            <Table
              caption="Duration of each Screening step"
              hideCaption
              columns={stepColumns}
              rows={stepRows}
              rowKey={(r) => String(typeof r.label === "string" ? r.label : "total")}
              empty="No steps yet."
            />
          </Card>

          {app.open_queue_items.length > 0 && (
            <Card title="Open in the review queue">
              <ul>
                {app.open_queue_items.map((q) => (
                  <li key={q.id}>
                    <Link to="/queue">{queueKindLabel[q.kind]}</Link> from {formatDateTime(q.created_at)}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card title="Files">
            {app.files.length === 0 ? (
              <p className="detail-muted">No files. Phase 2 collects no documents.</p>
            ) : (
              <ul>
                {app.files.map((f) => (
                  <li key={f.url}>
                    <a href={f.url}>{f.name}</a>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Candidate's other Applications">
            {app.other_applications.length === 0 ? (
              <p className="detail-muted">None.</p>
            ) : (
              <ul>
                {app.other_applications.map((o) => (
                  <li key={o.id}>
                    <Link to={`/applications/${o.id}`}>{formatDate(o.created_at)}</Link>: {stageLabel[o.stage]}
                    {o.outcome ? `, ${outcomeLabel[o.outcome]}` : o.status === "open" ? ", open" : ""}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="detail-col detail-col--transcript">
          <Card title="Transcript">
            <Transcript messages={app.messages} selectedId={selectedMessage} labels={app.message_labels ?? []} />
          </Card>
          <TakeoverPanel application={app} />
        </div>

        <div className="detail-col" role="group" aria-label="Event history" tabIndex={0}>
          <Card title="Timeline">
            {app.events.length > TIMELINE_WINDOW && (
              <Button
                variant="quiet"
                className="detail-timeline__toggle"
                aria-expanded={allEvents}
                aria-controls={timelineId}
                onClick={() => setAllEvents((v) => !v)}
              >
                {allEvents ? `Show latest ${TIMELINE_WINDOW}` : `Show all ${app.events.length} events`}
              </Button>
            )}
            <ol id={timelineId} className="timeline detail-timeline">
              {(allEvents ? app.events : app.events.slice(-TIMELINE_WINDOW)).map((e) => (
                <li key={e.id} className="detail-timeline__item">
                  <b>{eventLabel[e.kind] ?? e.kind}</b>
                  {eventDetail(e) && (
                    <span className="detail-muted detail-timeline__detail" title={eventDetail(e) ?? undefined}>
                      {" "}
                      {eventDetail(e)}
                    </span>
                  )}
                  <span className="detail-muted detail-timeline__meta" title={`${actorOf(e)} · ${formatDateTime(e.created_at)}`}>
                    {actorOf(e)} · {formatDateTime(e.created_at)}
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        {showPanel && panelOpen && <OperatorPanel id={panelId} detail={app} onShowMessage={setSelectedMessage} />}
      </div>
    </div>
  );
}

/** Says why the summary has no written part, when the backend is retrying it or has stopped. */
function ProseStateNote({ summary }: { summary: Summary }) {
  const state = proseState(summary);
  if (state === null) return null;
  return (
    <p className="detail-muted" role="status" data-prose-state={state}>
      {proseStateCopy[state]}
    </p>
  );
}
