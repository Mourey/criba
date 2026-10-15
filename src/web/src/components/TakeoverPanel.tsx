import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api/client.ts";
import type { ApplicationDetail } from "../api/types.ts";
import { useAuth } from "../auth/context.ts";
import { appendMessage, applyControl } from "../lib/applicationCache.ts";
import * as copy from "../lib/labels/recruiter.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "./ui/Button.tsx";
import { Card } from "./ui/Card.tsx";
import "./ui/ui.css";

/**
 * Takeover (ADR 0020): pause the Screening Agent, relay messages both ways (the transcript above
 * updates live from the Application's event stream), then hand back with a summary.
 */
export function TakeoverPanel({ application }: { application: ApplicationDetail }) {
  const { me, role } = useAuth();
  const queryClient = useQueryClient();
  const key = ["application", application.id];
  const [text, setText] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mine = application.control === "human" && application.control_user_id === me?.user.id;
  const heldByOther = application.control === "human" && !mine;
  const closed = application.status === "closed";
  // The API lets an Operator hand back a Takeover someone else holds (for example one left open
  // by a Recruiter who went home); the summary is then generated from the transcript.
  const canForceHandBack = heldByOther && role === "operator";

  const setApp = (update: (app: ApplicationDetail) => ApplicationDetail) =>
    queryClient.setQueryData<ApplicationDetail>(key, (old) => (old ? update(old) : old));
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : copy.takeoverFailed);
  // WhatsApp allows only approved templates 24 hours after the Candidate's last message (409).
  const failSend = (e: unknown) => (e instanceof ApiError && e.code === "outside_whatsapp_window" ? setError(copy.takeoverOutsideWindow) : fail(e));

  const start = useMutation({
    mutationFn: () => api.startTakeover(application.id),
    onSuccess: (state) => {
      setError(null);
      setApp((app) => applyControl(app, state));
      void queryClient.invalidateQueries({ queryKey: key });
    },
    onError: fail,
  });

  const send = useMutation({
    mutationFn: (body: string) => api.sendTakeoverMessage(application.id, body),
    onSuccess: (message) => {
      setError(null);
      setText("");
      setApp((app) => appendMessage(app, message));
    },
    onError: failSend,
  });

  const handBack = useMutation({
    mutationFn: () => api.handBack(application.id, summary.trim() || null),
    onSuccess: (state) => {
      setError(null);
      setSummary("");
      setApp((app) => applyControl(app, state));
      void queryClient.invalidateQueries({ queryKey: key });
    },
    onError: fail,
  });

  const onSend = (event: FormEvent) => {
    event.preventDefault();
    if (text.trim()) send.mutate(text.trim());
  };

  return (
    <section className="rp-takeover" aria-labelledby="takeover-heading">
      <Card>
        <h2 id="takeover-heading" className="rp-takeover__title">
          {copy.takeoverTitle}
        </h2>
        <div className="rp-takeover__body">
          {error && (
            <p className="rp-alert" role="alert">
              {error}
            </p>
          )}

          {application.control === "agent" && (
            <>
              <p className="rp-muted">{copy.takeoverAgentHolds}</p>
              <div className="rp-actions">
                <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending || closed}>
                  {copy.takeoverStart}
                </Button>
              </div>
              {closed && <p className="rp-muted">{copy.takeoverClosed}</p>}
            </>
          )}

          {heldByOther && (
            <>
              <p className="rp-note">{copy.takeoverHeldBy(application.control_user_name ?? null, canForceHandBack)}</p>
              {canForceHandBack && (
                <div className="rp-actions">
                  <Button onClick={() => handBack.mutate()} disabled={handBack.isPending}>
                    {copy.takeoverHandBack}
                  </Button>
                </div>
              )}
            </>
          )}

          {mine && (
            <>
              <p className="rp-note">{copy.takeoverMine}</p>
              <form className="rp-composer" onSubmit={onSend}>
                <label className="ui-sr-only" htmlFor="takeover-text">
                  {copy.takeoverMessageLabel}
                </label>
                <textarea
                  id="takeover-text"
                  className="rp-textarea"
                  rows={2}
                  value={text}
                  maxLength={4000}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={copy.takeoverMessagePlaceholder}
                />
                <Button variant="primary" type="submit" disabled={!text.trim() || send.isPending}>
                  {copy.takeoverSend}
                </Button>
              </form>
              <label className="rp-field">
                {copy.takeoverSummaryLabel}
                <textarea className="rp-textarea" rows={2} maxLength={2000} value={summary} onChange={(e) => setSummary(e.target.value)} />
              </label>
              <div className="rp-actions">
                <Button onClick={() => handBack.mutate()} disabled={handBack.isPending}>
                  {copy.takeoverHandBack}
                </Button>
              </div>
            </>
          )}
        </div>
      </Card>
    </section>
  );
}
