import type { ChatMessage } from "../api/types.ts";
import { Icon } from "../components/ui/Icon.tsx";
import "../components/ui/ui.css";
import { stage } from "../lib/labels.ts";
import "../styles/surfaces/stage.css";
import { bubbleTime, type PhoneCountry, type TickState } from "./phone.ts";

type BubbleSide = "left" | "right" | "center";

/** The Candidate's messages are on the right, the agent's and a team member's on the left, notices in the middle. */
function bubbleSide(message: ChatMessage): BubbleSide {
  if (message.sender === "system") return "center";
  return message.direction === "inbound" ? "right" : "left";
}

export interface ChatBubbleProps {
  message: ChatMessage;
  country: PhoneCountry;
  /** The Candidate's tick, derived from the thread; null for every other message. */
  tick: TickState | null;
  /** The options of this message are live (it is the conversation's `answerableId`). */
  answerable: boolean;
  /** The list sheet of this message is open. */
  listOpen: boolean;
  /** For a list that was answered by a tap: the label of the option the Candidate chose. */
  chosen?: string | null;
  onTap: (message: ChatMessage, optionId: string) => void;
  onOpenList: (message: ChatMessage, trigger: HTMLButtonElement) => void;
}

function Tick({ state }: { state: TickState }) {
  return (
    <span className={`phone-tick phone-tick--${state}`} role="img" aria-label={stage.tickLabel[state]}>
      <Icon name="check" size="sm" />
      {state !== "sent" && <Icon name="check" size="sm" />}
    </span>
  );
}

export function ChatBubble({ message: m, country, tick, answerable, listOpen, chosen, onTap, onOpenList }: ChatBubbleProps) {
  const side = bubbleSide(m);
  return (
    <div className="phone-msg" data-side={side} data-sender={m.sender}>
      <div className="phone-bubble">
        {m.sender === "human" && <span className="phone-who">{m.sender_label ?? stage.teamFallback}</span>}
        {m.text && <p className="phone-text">{m.text}</p>}
        {m.links.map((l) => (
          <a key={l.url} className="phone-link" href={l.url} target="_blank" rel="noreferrer">
            {l.label}
          </a>
        ))}
        {m.attachments.map((a) => (
          <a key={a.url} className="phone-doc" href={a.url} download={a.filename} aria-label={a.filename}>
            <span className="phone-doc__icon">
              <Icon name="calendar" size="lg" />
            </span>
            <span className="phone-doc__text">
              <span className="phone-doc__name">{a.filename}</span>
              <span className="phone-doc__type">{stage.inviteType}</span>
            </span>
          </a>
        ))}
        {side !== "center" && (
          <span className="phone-meta">
            <time dateTime={m.created_at}>{bubbleTime(m.created_at, country)}</time>
            {tick && <Tick state={tick} />}
          </span>
        )}
        {m.interactive?.style === "list" && (
          <button
            type="button"
            className="phone-list"
            disabled={!answerable}
            aria-haspopup="dialog"
            aria-expanded={listOpen}
            onClick={(event) => onOpenList(m, event.currentTarget)}
          >
            <Icon name="chevronDown" size="sm" />
            {chosen ? `${stage.optionsChosen} ${chosen}` : stage.optionsButton}
          </button>
        )}
      </div>
      {m.interactive?.style === "buttons" && (
        <div className="phone-replies" role="group" aria-label={stage.replyGroup}>
          {m.interactive.options.map((o) => (
            <button key={o.id} type="button" className="phone-reply" disabled={!answerable} onClick={() => onTap(m, o.id)}>
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
