import type { MessageLabel } from "../api/types.ts";
import { formatDateTime } from "../lib/format.ts";
import { stageLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/recruiter.ts";
import { messageLabelWords } from "../lib/messageLabels.ts";
import "../styles/surfaces/recruiter.css";

/** One message's labels on one line, plain text with no colour: context, not a warning. */
export function MessageLabelStrip({ label }: { label: MessageLabel }) {
  const words = messageLabelWords(label);
  if (words.length === 0) return null;
  return (
    <p className="rp-labels" title={copy.labelsStripName} data-testid="message-labels">
      {words.map((w, i) => (
        <span key={w} className="rp-labels__item">
          {i > 0 && <span aria-hidden="true">·</span>}
          {w}
        </span>
      ))}
    </p>
  );
}

/** Every message's labels, oldest first, with the time (`timeOf`, UTC by default) and the Stage each arrived at: the Demo stage's Recruiter view. */
export function MessageLabelList({ labels, timeOf = formatDateTime }: { labels: readonly MessageLabel[]; timeOf?: (iso: string) => string }) {
  return (
    <div className="rp-label-list">
      <p className="rp-label-list__lede">{copy.labelsFixedLine}</p>
      {labels.length === 0 ? (
        <p className="rp-label-list__none">{copy.labelsNone}</p>
      ) : (
        <ol className="rp-label-list__rows" aria-label={copy.labelsListTitle}>
          {labels.map((label) => (
            <li key={label.message_id} className="rp-label-list__row">
              <span className="rp-label-list__meta">
                <time dateTime={label.at}>{timeOf(label.at)}</time>
                {label.stage ? ` · ${stageLabel[label.stage]}` : ""}
              </span>
              <MessageLabelStrip label={label} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
