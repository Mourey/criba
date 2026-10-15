import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, type ConversationDetail } from "../api/client.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { Transcript } from "../components/Transcript.tsx";
import { formatDateTime } from "../lib/format.ts";
import * as copy from "../lib/labels/conversations.ts";
import { ConversationName, ConversationStage, SpokenTag } from "./Conversations.tsx";
import { NotFound } from "./NotFound.tsx";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import "../components/ui/ui.css";

/** The key /test-chat reads its conversation from (TestChat.tsx), so Resume goes through its own load path. */
const TEST_CHAT_KEY = "criba.testChat.conversation";

/** Resume is offered for a typed conversation while nothing has been said yet, or while the latest
 * message's Application is open. /test-chat cannot carry on a voice call, so a voice one never offers it. */
function canResume(c: ConversationDetail): boolean {
  if (c.channel === "voice") return false;
  return c.messages.length === 0 || c.application?.status === "open";
}

/** One Test chat or Demo stage conversation, read-only, with Resume while it is open (CH5). */
export function Conversation() {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [resumeFailed, setResumeFailed] = useState(false);
  const query = useQuery({
    queryKey: ["conversation", id],
    queryFn: () => api.getConversation(id),
    enabled: allowed && id !== "",
  });

  if (!allowed) return <NotFound />;

  const back = <Link to="/conversations">{copy.backToList}</Link>;

  if (query.isPending) {
    return (
      <div className="rp-skeleton" aria-hidden="true">
        <Skeleton shape="block" />
      </div>
    );
  }
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <Card>
          <h1>{copy.notFound}</h1>
          <p>{copy.notFoundHint}</p>
          {back}
        </Card>
      );
    }
    return (
      <div className="stack">
        <h1>{copy.title}</h1>
        <ErrorState message={copy.viewLoadFailed(query.error.message)} onRetry={() => void query.refetch()} />
        {back}
      </div>
    );
  }

  const c = query.data;
  const application = c.application;

  const facts = [
    c.persona_id ? c.phone : null,
    copy.clientLabel[c.client],
    copy.startedAt(formatDateTime(c.created_at)),
    c.last_message_at ? copy.lastMessage(formatDateTime(c.last_message_at)) : null,
  ].filter((fact): fact is string => Boolean(fact));

  // Why there is no Resume, when there is one to give (a typed conversation with messages and no
  // Application has none).
  const noResume = c.channel === "voice" ? copy.spokenNoResume : application?.status === "closed" ? copy.closed : null;

  const resume = () => {
    try {
      window.sessionStorage.setItem(TEST_CHAT_KEY, c.conversation_id);
    } catch {
      setResumeFailed(true);
      return;
    }
    setResumeFailed(false);
    navigate("/test-chat");
  };

  return (
    <div className="rp-stack">
      <div className="page-head rp-page-head">
        <div>
          <p className="rp-muted">{back}</p>
          <h1>
            <ConversationName persona_id={c.persona_id} phone={c.phone} />
          </h1>
          <p className="muted">
            {facts.map((fact, i) => (
              <Fragment key={i}>
                {i > 0 && " · "}
                <span>{fact}</span>
              </Fragment>
            ))}
          </p>
        </div>
        <div className="row">
          <SpokenTag channel={c.channel} />
          <ConversationStage application={application} />
          {application && (
            <Link className="rp-link" to={`/applications/${application.id}`}>
              {copy.openApplication}
            </Link>
          )}
        </div>
      </div>

      {c.shared_phone && <p className="rp-note">{copy.sharedPhone}</p>}

      {canResume(c) ? (
        <div className="rp-actions">
          <Button variant="primary" onClick={resume}>
            {copy.resume}
          </Button>
          <span className="rp-muted">{copy.resumeHint}</span>
          <span className="rp-muted" role="status">
            {resumeFailed ? copy.resumeFailed : ""}
          </span>
        </div>
      ) : (
        noResume && <p className="rp-muted">{noResume}</p>
      )}

      <Transcript messages={c.messages} />
    </div>
  );
}
