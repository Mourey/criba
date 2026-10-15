import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import type { ChatLocale, ChatMessage } from "../api/types.ts";
import { useConversation } from "../api/useConversation.ts";
import { formatDateTime } from "../lib/format.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { Field } from "../components/ui/Field.tsx";
import "../components/ui/ui.css";
import "../styles/surfaces/stage.css";

const STORAGE_KEY = "criba.testChat.conversation";

const defaultLocale = { ES: "es-ES", MX: "es-MX" } as const;

const localeNames: Record<ChatLocale, string> = {
  "es-MX": "Spanish (Mexico)",
  "es-ES": "Spanish (Spain)",
  en: "English",
};

/**
 * A web chat client for the web chat channel (contracts/webchat.openapi.yaml). It is the Candidate
 * side: no login, an unguessable conversation id, replies read by long poll. The demo and the
 * simulator talk to the same endpoints.
 */
export function TestChat() {
  const {
    conversationId,
    messages,
    typing,
    error,
    personaName,
    language,
    busy,
    answerableId: answerable,
    start: startConversation,
    send: sendMessage,
    tap,
    reset,
  } = useConversation({ storageKey: STORAGE_KEY });

  const [country, setCountry] = useState<"ES" | "MX">("MX");
  const [locale, setLocale] = useState<ChatLocale>("es-MX");
  const [phone, setPhone] = useState("");
  const [text, setText] = useState("");
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    log.current?.scrollTo?.({ top: log.current.scrollHeight });
  }, [messages.length, typing]);

  const start = (event: FormEvent) => {
    event.preventDefault();
    void startConversation({ country, locale, phone: phone.trim() || null, client: "test_chat" });
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (!conversationId || !text.trim()) return;
    const body = text;
    setText("");
    if (!(await sendMessage(body))) setText(body);
  };

  return (
    <main className="public testchat" lang={locale.startsWith("en") ? "en" : "es"}>
      <div className="row spread">
        <h1>Test chat</h1>
        <Link to="/applications">Back to the app</Link>
      </div>
      <p className="muted">You are the Candidate. This page uses the same web chat channel as the simulator and the demo.</p>

      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}

      {!conversationId && (
        <Card>
          <form className="stack" onSubmit={start}>
            <label className="field">
              Country
              <select
                value={country}
                onChange={(e) => {
                  const c = e.target.value as "ES" | "MX";
                  setCountry(c);
                  setLocale(defaultLocale[c]);
                }}
              >
                <option value="MX">Mexico</option>
                <option value="ES">Spain</option>
              </select>
            </label>
            <label className="field">
              Language
              <select value={locale} onChange={(e) => setLocale(e.target.value as ChatLocale)}>
                <option value="es-MX">Spanish (Mexico)</option>
                <option value="es-ES">Spanish (Spain)</option>
                <option value="en">English</option>
              </select>
            </label>
            <Field
              label="Phone (optional, identifies the Candidate across conversations)"
              type="tel"
              placeholder={country === "ES" ? "+34612345678" : "+525512345678"}
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
            <Button variant="primary" type="submit" disabled={busy}>
              Start conversation
            </Button>
          </form>
        </Card>
      )}

      {conversationId && (
        <section aria-label="Conversation">
          <Card>
            {(personaName || language) && (
              <p className="muted" data-testid="chat-meta">
                {personaName && (
                  <>
                    Agent: <strong>{personaName}</strong>
                  </>
                )}
                {personaName && language && " · "}
                {language && <>Language: {localeNames[language]}</>}
              </p>
            )}
            <div className="chat" ref={log} role="log" aria-live="polite" aria-label="Messages">
              {messages.length === 0 && <p className="muted">Say hello to start. The Candidate writes first.</p>}
              {messages.map((m) => (
                <ChatBubble key={m.id} message={m} answerable={answerable === m.id} onTap={tap} />
              ))}
              {typing && <div className="bubble muted typing">Typing...</div>}
            </div>
            <form className="composer" onSubmit={send}>
              <label className="sr-only" htmlFor="chat-text">
                Message
              </label>
              <input id="chat-text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a message" autoComplete="off" />
              <Button variant="primary" type="submit" disabled={!text.trim()}>
                Send
              </Button>
            </form>
            <p>
              <Button onClick={reset}>New conversation</Button>
            </p>
          </Card>
        </section>
      )}
    </main>
  );
}

function ChatBubble(props: { message: ChatMessage; answerable: boolean; onTap: (m: ChatMessage, optionId: string) => void }) {
  const { message: m } = props;
  const cls =
    m.sender === "system" ? "bubble system" : m.sender === "human" ? "bubble human" : m.direction === "inbound" ? "bubble out" : "bubble";
  return (
    <div className={cls} data-sender={m.sender}>
      {m.sender !== "system" && <span className="who-line">{m.direction === "inbound" ? "You" : (m.sender_label ?? "Team")}</span>}
      {m.text}
      {m.links.map((l) => (
        <div key={l.url}>
          <a href={l.url} target="_blank" rel="noreferrer">
            {l.label}
          </a>
        </div>
      ))}
      {m.attachments.map((a) => (
        <div key={a.url}>
          <a href={a.url} download={a.filename}>
            {a.filename}
          </a>
        </div>
      ))}
      {m.interactive && (
        <div className="opts">
          {m.interactive.options.map((o) => (
            <Button key={o.id} disabled={!props.answerable} onClick={() => props.onTap(m, o.id)}>
              {o.label}
            </Button>
          ))}
        </div>
      )}
      <time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
    </div>
  );
}
