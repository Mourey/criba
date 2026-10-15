import { useInfiniteQuery } from "@tanstack/react-query";
import { useId } from "react";
import { Link } from "react-router";
import { api, type ConversationItem } from "../api/client.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { StageOutcome } from "../components/Flags.tsx";
import { formatDateTime } from "../lib/format.ts";
import * as copy from "../lib/labels/conversations.ts";
import { PERSONAS } from "../stage/playback.ts";
import { NotFound } from "./NotFound.tsx";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import { Tag } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";

/** Rows the skeleton draws while the first page loads. */
const SKELETON_ROWS = 4;

type ConversationFacts = Pick<ConversationItem, "persona_id" | "phone">;

/** The persona's name (from the bundled personas), "Unknown persona" for an id not there, else the phone. */
export function ConversationName({ persona_id, phone }: ConversationFacts) {
  if (persona_id) return PERSONAS.find((p) => p.id === persona_id)?.identity.name ?? copy.unknownPersona;
  return phone ?? copy.noPhone;
}

/** "Spoken" for a voice conversation (ADR 0067); nothing for a typed one. */
export function SpokenTag({ channel }: Pick<ConversationItem, "channel">) {
  return channel === "voice" ? <Tag>{copy.spoken}</Tag> : null;
}

/** Stage and Outcome as on Applications, or "No Application yet". */
export function ConversationStage({ application }: Pick<ConversationItem, "application">) {
  if (!application) return <span className="rp-sub rp-sub--lead">{copy.noApplication}</span>;
  return <StageOutcome stage={application.stage} outcome={application.outcome} />;
}

const columns: TableColumn<ConversationItem>[] = [
  {
    key: "who",
    header: copy.columns.who,
    render: (c) => (
      <>
        <Link className="rp-link" to={`/conversations/${c.conversation_id}`}>
          <ConversationName persona_id={c.persona_id} phone={c.phone} />
        </Link>{" "}
        <SpokenTag channel={c.channel} />
        {c.persona_id && c.phone && (
          <div>
            <span className="rp-sub rp-sub--lead">{c.phone}</span>
          </div>
        )}
      </>
    ),
  },
  { key: "client", header: copy.columns.client, render: (c) => copy.clientLabel[c.client] },
  { key: "stage", header: copy.columns.stageOutcome, render: (c) => <ConversationStage application={c.application} /> },
  {
    key: "time",
    header: copy.columns.time,
    mono: true,
    render: (c) => (
      <>
        <time dateTime={c.created_at}>{formatDateTime(c.created_at)}</time>
        <div>
          <span className="rp-sub rp-sub--lead">
            {c.last_message_at ? copy.lastMessage(formatDateTime(c.last_message_at)) : copy.noMessage}
          </span>
        </div>
      </>
    ),
  },
];

/** The Operator's list of Test chat and Demo stage conversations, newest first (CH4). */
export function Conversations() {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  const introId = useId();
  const list = useInfiniteQuery({
    queryKey: ["conversations"],
    queryFn: ({ pageParam }) => api.listConversations(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: allowed,
  });

  if (!allowed) return <NotFound />;

  const items = list.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <div className="page-head rp-page-head">
        <div>
          <h1>{copy.title}</h1>
          <p id={introId} className="muted">
            {copy.intro}
          </p>
        </div>
        <Link className="ui-btn ui-btn--primary" to="/test-chat">
          {copy.newConversation}
        </Link>
      </div>

      {list.isError && <ErrorState message={copy.loadFailed(list.error.message)} onRetry={() => void list.refetch()} />}

      {(list.isPending || list.data) && (
        <div className="rp-scroll" role="region" aria-label={copy.tableCaption} tabIndex={0} aria-busy={list.isPending}>
          {list.isPending ? (
            <div className="rp-skeleton" aria-hidden="true">
              {Array.from({ length: SKELETON_ROWS }, (_, i) => (
                <Skeleton key={i} shape="block" />
              ))}
            </div>
          ) : (
            <Table
              caption={copy.tableCaption}
              hideCaption
              columns={columns}
              rows={items}
              rowKey={(c) => c.conversation_id}
              empty={
                <>
                  <p className="rp-empty__title">{copy.empty}</p>
                  <p className="rp-empty__hint">{copy.emptyHint}</p>
                </>
              }
            />
          )}
        </div>
      )}
      {list.hasNextPage && (
        <div className="rp-actions rp-more-rows">
          <Button onClick={() => void list.fetchNextPage()} loading={list.isFetchingNextPage} aria-describedby={introId}>
            {copy.loadMore}
          </Button>
        </div>
      )}
    </>
  );
}
