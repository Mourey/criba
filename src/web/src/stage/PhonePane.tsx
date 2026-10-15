import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatMessage } from "../api/types.ts";
import type { Conversation } from "../api/useConversation.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import "../components/ui/ui.css";
import { countryLabel, stage } from "../lib/labels.ts";
import "../styles/surfaces/stage.css";
import { ChatBubble } from "./ChatBubble.tsx";
import { countryOfPhone, initials, isValidPhone, LOCALE_OF, tickState, type PhoneCountry } from "./phone.ts";

export interface PhonePaneProps {
  conversation: Conversation;
  /** The Candidate's phone for this Take. */
  phone: string;
  /** The start card's Country changed: the session makes a fresh phone for it. */
  onCountryChange: (country: PhoneCountry) => void;
  /** Controlled composer text, so a player can show the Candidate typing. Without it the composer is uncontrolled. */
  draft?: string;
  onDraftChange?: (text: string) => void;
}

/** By id, each id once: a page that repeats a message never shows it twice. */
function ordered(messages: readonly ChatMessage[]): ChatMessage[] {
  const byId = new Map<number, ChatMessage>();
  for (const m of messages) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

/** The label of the option the Candidate tapped on a list, read from the reply that points at it. */
function chosenOption(message: ChatMessage, messages: readonly ChatMessage[]): string | null {
  if (message.interactive?.style !== "list") return null;
  const reply = messages.find((m) => m.reply_to?.message_id === message.id);
  return message.interactive.options.find((o) => o.id === reply?.reply_to?.option_id)?.label ?? null;
}

/** The log follows new messages only while the reader is already at the bottom. */
const BOTTOM_SLACK = 32;

export function PhonePane({ conversation, phone, onCountryChange, draft, onDraftChange }: PhonePaneProps) {
  const { conversationId, typing, runFinished, error, personaName, language, busy, answerableId } = conversation;
  const country = countryOfPhone(phone) ?? "MX";
  const messages = useMemo(() => ordered(conversation.messages), [conversation.messages]);
  // One name for the whole Take, and the next one: the last name the channel sent is kept, and before
  // the first it is the channel's default, so the header never changes under the viewer.
  const [known, setKnown] = useState<string | null>(null);
  if (personaName && personaName !== known) setKnown(personaName);
  const name = personaName ?? known ?? stage.personaFallback;

  const [openList, setOpenList] = useState<number | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [inner, setInner] = useState("");
  const controlled = draft !== undefined;
  const text = controlled ? draft : inner;
  const inputId = useId();
  const startTitle = useId();

  // The sheet shows only while its list is the live one.
  const sheetMessage = openList !== null && answerableId === openList ? messages.find((m) => m.id === openList) : undefined;
  const sheetOptions = sheetMessage?.interactive?.style === "list" ? sheetMessage.interactive.options : [];

  useEffect(() => {
    if (sheetMessage) sheet.current?.querySelector("button")?.focus();
  }, [sheetMessage]);

  useEffect(() => {
    const el = log.current;
    if (el && atBottom.current) el.scrollTo?.({ top: el.scrollHeight });
  }, [messages.length, typing, conversationId, sheetMessage]);

  const setText = (value: string) => {
    if (!controlled) setInner(value);
    onDraftChange?.(value);
  };

  const closeSheet = () => {
    setOpenList(null);
    trigger.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape" && sheetMessage) {
      event.stopPropagation();
      closeSheet();
    }
  };

  const onOpenList = (message: ChatMessage, button: HTMLButtonElement) => {
    trigger.current = button;
    setOpenList((current) => (current === message.id ? null : message.id));
  };

  const tap = (message: ChatMessage, optionId: string) => {
    void conversation.tap(message, optionId);
    if (sheetMessage) closeSheet();
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    const body = text;
    setText("");
    atBottom.current = true;
    if (!(await conversation.send(body))) setText(body);
  };

  const start = (event: FormEvent) => {
    event.preventDefault();
    if (!isValidPhone(phone, country)) return;
    void conversation.start({ country, locale: LOCALE_OF[country], phone, client: "demo" });
  };

  return (
    <section className="phone" aria-label={stage.phoneRegion} onKeyDown={onKeyDown}>
      <header className="phone-header" data-testid="phone-header">
        <span className="phone-avatar" aria-hidden="true">
          {initials(name)}
        </span>
        <div className="phone-header__text">
          <p className="phone-name">{name}</p>
          <p className="phone-status">{typing ? stage.statusTyping : stage.statusOnline}</p>
        </div>
      </header>

      {conversationId ? (
        <div
          ref={log}
          className="phone-chat"
          role="log"
          tabIndex={0}
          aria-live="polite"
          aria-label={stage.messagesLabel}
          lang={language ?? LOCALE_OF[country]}
          onScroll={(event) => {
            const el = event.currentTarget;
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
          }}
        >
          {messages.length === 0 && <p className="phone-empty">{stage.emptyChat}</p>}
          {messages.map((m) => (
            <ChatBubble
              key={m.id}
              message={m}
              country={country}
              tick={m.direction === "inbound" && m.sender === "candidate" ? tickState(m, messages, runFinished) : null}
              answerable={answerableId === m.id}
              listOpen={sheetMessage?.id === m.id}
              chosen={chosenOption(m, messages)}
              onTap={tap}
              onOpenList={onOpenList}
            />
          ))}
          {typing && (
            <div className="phone-typing" data-testid="phone-typing" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          )}
        </div>
      ) : (
        <div className="phone-chat phone-chat--idle">
          <form className="phone-start" aria-labelledby={startTitle} onSubmit={start}>
            <h2 id={startTitle} className="phone-start__title">
              {stage.startHeading}
            </h2>
            <label className="phone-start__field">
              <span className="phone-start__label">{stage.countryField}</span>
              <select className="phone-start__select" value={country} onChange={(event) => onCountryChange(event.target.value as PhoneCountry)}>
                <option value="MX">{countryLabel.MX}</option>
                <option value="ES">{countryLabel.ES}</option>
              </select>
            </label>
            <dl className="phone-start__phone">
              <dt>{stage.phoneField}</dt>
              <dd data-testid="start-phone">{phone}</dd>
            </dl>
            <Button type="submit" variant="primary" loading={busy} disabled={!isValidPhone(phone, country)}>
              {stage.startButton}
            </Button>
          </form>
        </div>
      )}

      {sheetMessage && (
        <div ref={sheet} className="phone-sheet" role="dialog" aria-modal="false" aria-label={stage.optionsSheet}>
          {sheetOptions.map((o) => (
            <button key={o.id} type="button" className="phone-sheet__option" onClick={() => tap(sheetMessage, o.id)}>
              {o.label}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p className="phone-alert" role="alert">
          {error}
        </p>
      )}

      {conversationId && (
        <form className="phone-composer" onSubmit={send}>
          <label className="ui-sr-only" htmlFor={inputId}>
            {stage.messageLabel}
          </label>
          <input
            id={inputId}
            className="phone-composer__input"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={stage.messagePlaceholder}
            autoComplete="off"
          />
          <Button type="submit" variant={text.trim() ? "primary" : "secondary"} disabled={!text.trim()}>
            {stage.sendButton}
          </Button>
        </form>
      )}
    </section>
  );
}
