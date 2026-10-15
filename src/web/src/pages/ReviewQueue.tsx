import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client.ts";
import type { ApplicationSummary, QueueItem, QueueKind } from "../api/types.ts";
import { FlagTags } from "../components/Flags.tsx";
import { RejectionForm } from "../components/RejectionForm.tsx";
import { candidateName, formatDateTime, formatDuration } from "../lib/format.ts";
import { escalationReasonLabel, outcomeLabel, queueKindLabel, rejectionReasonLabel, resolutionLabel, stageLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/recruiter.ts";
import { useEventStream } from "../lib/sse.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import { Tag } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";

type Tab = copy.QueueTab;

const REJECTIONS_KEY = ["applications", "rejections"] as const;

const rejectionColumns: TableColumn<ApplicationSummary>[] = [
  {
    key: "candidate",
    header: copy.rejectionColumns.candidate,
    render: (a) => (
      <Link className="rp-link" to={`/applications/${a.id}`}>
        {candidateName(a.candidate)}
      </Link>
    ),
  },
  { key: "stage", header: copy.rejectionColumns.stage, render: (a) => stageLabel[a.stage] },
  {
    key: "reason",
    header: copy.rejectionColumns.reason,
    render: (a) => (
      <>
        {a.rejection_reason ? rejectionReasonLabel[a.rejection_reason] : "-"}
        {a.rejection_note && (
          <div className="rp-sub" title={a.rejection_note}>
            {a.rejection_note}
          </div>
        )}
      </>
    ),
  },
  { key: "rejected", header: copy.rejectionColumns.rejected, mono: true, render: (a) => formatDateTime(a.closed_at) },
  { key: "total", header: copy.rejectionColumns.total, mono: true, align: "end", render: (a) => formatDuration(a.total_seconds) },
];

/** The shapes of the cards or rows about to load; decorative, so hidden from assistive technology. */
function ListSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="rp-skeleton" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} shape="block" />
      ))}
    </div>
  );
}

export function ReviewQueue() {
  const [params, setParams] = useSearchParams();
  const tab = (copy.TABS.find((t) => t.id === params.get("tab"))?.id ?? "approval") as Tab;
  const status = params.get("status") === "resolved" ? "resolved" : "open";
  const queryClient = useQueryClient();

  // No placeholder on either query: a changed `status` shows the skeleton, never the other list's items.
  const queue = useQuery({
    queryKey: ["queue", status],
    queryFn: () => api.listQueue({ status, limit: 200 }),
  });
  const rejections = useQuery({
    queryKey: REJECTIONS_KEY,
    queryFn: () => api.listApplications({ outcome: ["rejection"], status: "closed", sort: "-updated_at", limit: 50 }),
    enabled: tab === "rejection",
  });

  // Live updates: any queue change in scope refreshes the lists, and so does each (re)connect,
  // since the stream does not replay what changed before it opened. The Rejections list is a
  // separate Applications query. A Rejection made from an Application's own page opens and
  // resolves no queue item, so the backend polls `rejection` events and sends a `queue` frame
  // for it (`kind: "rejection"`); a Rejection resolved from an Approval is covered by that
  // item's resolved frame. Every frame refreshes the Rejections list, and so does the
  // (re)connect, which the backend forces every few minutes and which catches anything missed
  // while the stream was down. The query also refetches on window focus (React Query default).
  const refreshQueue = () => {
    void queryClient.invalidateQueries({ queryKey: ["queue"] });
    void queryClient.invalidateQueries({ queryKey: REJECTIONS_KEY });
  };
  useEventStream(api.queueStreamUrl(), ["queue"], refreshQueue, refreshQueue);

  const items = queue.data?.items ?? [];
  const byKind = (kind: QueueKind) => items.filter((i) => i.kind === kind);
  // No count until the list has loaded: "(0)" before the answer would say there is nothing.
  const count = (id: Tab) => (id === "rejection" || !queue.data ? null : byKind(id).length);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const empty = status === "open" ? copy.queueEmpty : copy.queueEmptyResolved;

  return (
    <>
      <div className="page-head rp-page-head">
        <h1>{copy.queueTitle}</h1>
        <label className="rp-field">
          {copy.queueShow}
          <select className="rp-select" value={status} onChange={(e) => setParam("status", e.target.value)}>
            <option value="open">{copy.queueShowOpen}</option>
            <option value="resolved">{copy.queueShowResolved}</option>
          </select>
        </label>
      </div>

      <div className="rp-tabs" role="tablist" aria-label={copy.queueTabsLabel}>
        {copy.TABS.map((t) => (
          <button key={t.id} type="button" className="rp-tab" role="tab" aria-selected={tab === t.id} onClick={() => setParam("tab", t.id)}>
            {t.label}
            {count(t.id) !== null ? ` (${count(t.id)})` : ""}
          </button>
        ))}
      </div>

      {queue.isError && (
        <div className="rp-error">
          <ErrorState message={copy.queueLoadFailed(queue.error.message)} onRetry={() => void queue.refetch()} />
        </div>
      )}

      {tab !== "rejection" && (
        <div className="rp-queue" role="tabpanel" aria-label={copy.TABS.find((t) => t.id === tab)?.label}>
          {queue.isPending && <ListSkeleton />}
          {queue.data && byKind(tab).length === 0 && (
            <div className="rp-empty">
              <p className="rp-empty__title">{empty[tab].title}</p>
              <p className="rp-empty__hint">{empty[tab].hint}</p>
            </div>
          )}
          {byKind(tab).map((item) => (
            <QueueCard key={item.id} item={item} />
          ))}
        </div>
      )}

      {tab === "rejection" && (
        <div className="rp-queue" role="tabpanel" aria-label={copy.TABS[3].label}>
          <p className="rp-muted">{copy.queueRejectionsIntro}</p>
          {rejections.isError && (
            <div className="rp-error">
              <ErrorState message={copy.rejectionsLoadFailed(rejections.error.message)} onRetry={() => void rejections.refetch()} />
            </div>
          )}
          {rejections.isPending && <ListSkeleton rows={5} />}
          {rejections.data && (
            <div className="rp-scroll" role="region" aria-label={copy.queueRejectionsTable} tabIndex={0}>
              <Table
                caption={copy.TABS[3].label}
                hideCaption
                columns={rejectionColumns}
                rows={rejections.data.items}
                rowKey={(a) => a.id}
                empty={<p className="rp-empty__title">{copy.queueRejectionsEmpty}</p>}
              />
            </div>
          )}
        </div>
      )}
    </>
  );
}

function QueueCard({ item }: { item: QueueItem }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const titleId = useId();
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const app = item.application;
  const open = item.status === "open";

  const done = () => {
    setError(null);
    void queryClient.invalidateQueries({ queryKey: ["queue"] });
    void queryClient.invalidateQueries({ queryKey: ["applications"] });
    void queryClient.invalidateQueries({ queryKey: ["application", app.id] });
  };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : copy.queueFailed);

  const approve = useMutation({ mutationFn: () => api.resolveApproval(item.id, { decision: "approved" }), onSuccess: done, onError: fail });
  const rejectIt = useMutation({
    mutationFn: (v: { reason: Parameters<typeof api.reject>[1]["reason"]; note: string | null }) =>
      api.resolveApproval(item.id, { decision: "rejected", reason: v.reason, note: v.note }),
    onSuccess: () => {
      setRejecting(false);
      done();
    },
    onError: fail,
  });
  const review = useMutation({
    mutationFn: (decision: "upheld" | "overturned") => api.resolveReview(item.id, { decision, note: note.trim() || null }),
    onSuccess: done,
    onError: fail,
  });
  const escalation = useMutation({
    mutationFn: (decision: "dismissed" | "takeover") => api.resolveEscalation(item.id, { decision, note: null }),
    onSuccess: (_resolved, decision) => {
      done();
      if (decision === "takeover") navigate(`/applications/${app.id}`);
    },
    onError: fail,
  });

  return (
    <article className="rp-qcard" aria-label={`${queueKindLabel[item.kind]} for ${candidateName(app.candidate)}`}>
      <div className="rp-qcard__head">
        <div className="rp-qcard__who">
          <h3 id={titleId} className="rp-qcard__title">
            <Link className="rp-link" to={`/applications/${app.id}`}>
              {candidateName(app.candidate)}
            </Link>{" "}
            <Tag>{queueKindLabel[item.kind]}</Tag>
          </h3>
          <p className="rp-sub">
            {app.locations.map((l) => l.name).join(", ")} · {stageLabel[app.stage]}
            {app.outcome ? ` · ${outcomeLabel[app.outcome]}` : ""} · waiting since {formatDateTime(item.created_at)}
          </p>
        </div>
        <FlagTags flags={app.flags} />
      </div>

      {item.kind === "escalation" && item.reason && (
        <p className="rp-qcard__text">
          <b>{escalationReasonLabel[item.reason]}.</b> {item.note}
        </p>
      )}
      {item.kind === "review" && (
        <p className="rp-qcard__text">
          The Candidate asked for a Review of the Knockout
          {app.knockout_criterion ? ` (${app.knockout_criterion === "zone" ? "Zone" : "driver's license"})` : ""}.
          {item.note ? ` ${item.note}` : ""}
        </p>
      )}
      {item.kind === "approval" && <p className="rp-qcard__text">{copy.queueApprovalLine}</p>}

      {error && (
        <p className="rp-alert" role="alert">
          {error}
        </p>
      )}

      {!open && (
        <p className="rp-sub">
          {item.resolution ? resolutionLabel[item.resolution] : copy.queueResolvedFallback} {formatDateTime(item.resolved_at)}
          {item.resolution_note ? `: ${item.resolution_note}` : ""}
        </p>
      )}

      {open && item.kind === "approval" && (
        <>
          <div className="rp-actions">
            <Button variant="primary" onClick={() => approve.mutate()} disabled={approve.isPending} aria-describedby={titleId}>
              {copy.queueApprove}
            </Button>
            <Button variant="danger" onClick={() => setRejecting((v) => !v)} aria-expanded={rejecting} aria-describedby={titleId}>
              {copy.queueReject}
            </Button>
          </div>
          {rejecting && (
            <div className="rp-qcard__reject">
              <RejectionForm
                busy={rejectIt.isPending}
                onSubmit={(reason, n) => rejectIt.mutate({ reason, note: n })}
                onCancel={() => setRejecting(false)}
              />
            </div>
          )}
        </>
      )}

      {open && item.kind === "review" && (
        <>
          <label className="rp-field">
            {copy.queueNote}
            <textarea className="rp-textarea" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="rp-actions">
            <Button onClick={() => review.mutate("upheld")} disabled={review.isPending} aria-describedby={titleId}>
              {copy.queueUpheld}
            </Button>
            <Button variant="primary" onClick={() => review.mutate("overturned")} disabled={review.isPending} aria-describedby={titleId}>
              {copy.queueOverturned}
            </Button>
          </div>
        </>
      )}

      {open && item.kind === "escalation" && (
        <div className="rp-actions">
          <Button variant="primary" onClick={() => escalation.mutate("takeover")} disabled={escalation.isPending} aria-describedby={titleId}>
            {copy.queueStartTakeover}
          </Button>
          <Button onClick={() => escalation.mutate("dismissed")} disabled={escalation.isPending} aria-describedby={titleId}>
            {copy.queueDismiss}
          </Button>
        </div>
      )}
    </article>
  );
}
