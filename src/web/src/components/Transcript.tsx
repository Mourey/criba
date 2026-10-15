import { useEffect, useRef } from "react";
import type { Message, MessageLabel } from "../api/types.ts";
import { MessageLabelStrip } from "./MessageLabels.tsx";
import { formatDateTime } from "../lib/format.ts";
import { senderLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/recruiter.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Tag } from "./ui/Tag.tsx";
import "./ui/ui.css";

function who(message: Message): string {
  if (message.sender === "human") return message.sender_name ?? senderLabel.human;
  return senderLabel[message.sender] ?? message.sender;
}

/**
 * The conversation as a log. It follows the newest message by setting its own scrollTop, so the window
 * never scrolls with it. `selectedId` marks one bubble with data-selected and scrolls the log to it.
 * `labels` puts Jev's labels (ADR 0075) under each typed Candidate message Jev read: words, never a score.
 */
export function Transcript({
  messages,
  selectedId = null,
  labels = [],
}: {
  messages: readonly Message[];
  selectedId?: number | null;
  labels?: readonly MessageLabel[];
}) {
  const labelOf = new Map(labels.map((l) => [l.message_id, l]));
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);
  useEffect(() => {
    const el = log.current;
    if (!el || selectedId === null) return;
    const bubble = el.querySelector<HTMLElement>(`[data-message-id="${selectedId}"]`);
    if (bubble) el.scrollTop += bubble.getBoundingClientRect().top - el.getBoundingClientRect().top;
  }, [selectedId]);

  if (messages.length === 0) return <p className="rp-transcript-empty">{copy.transcriptEmpty}</p>;
  // The log scrolls, so it takes the keyboard focus (WCAG 2.1.1) and keeps its name.
  return (
    <>
      {labels.length > 0 && <p className="rp-labels-lede">{copy.labelsFixedLine}</p>}
      <div ref={log} className="chat rp-transcript" role="log" aria-label={copy.transcriptTitle} tabIndex={0}>
        {messages.map((m) => {
          const cls =
            m.sender === "system" ? "bubble system" : m.sender === "human" ? "bubble human" : m.direction === "outbound" ? "bubble out" : "bubble";
          return (
            <div
              key={m.id}
              className={cls}
              data-sender={m.sender}
              data-message-id={m.id}
              data-selected={m.id === selectedId ? "true" : undefined}
            >
              {m.sender !== "system" && <span className="who-line">{who(m)}</span>}
              {m.body || (m.content_type === "media" ? `[${m.media_type ?? "media"}]` : "")}
              {m.interactive && (
                <div className="opts" role="group" aria-label={copy.transcriptOptionsOffered}>
                  {m.interactive.options.map((o) => (
                    <Tag key={o.id}>{o.label}</Tag>
                  ))}
                </div>
              )}
              {m.content_type === "button_reply" && <Tag>{copy.transcriptTapped}</Tag>}
              {m.direction === "inbound" && labelOf.has(m.id) && <MessageLabelStrip label={labelOf.get(m.id) as MessageLabel} />}
              <time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
            </div>
          );
        })}
      </div>
    </>
  );
}
