import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { chatApi } from "../api/client.ts";
import type { ChatMessage } from "../api/types.ts";
import { useConversation } from "../api/useConversation.ts";
import { VOICE_COPY, type VoiceCopy } from "../lib/labels/voice.ts";
import { useVoiceCall, type VoiceState } from "../voice/useVoiceCall.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { Icon } from "../components/ui/Icon.tsx";
import "../components/ui/ui.css";
import "../styles/surfaces/voice.css";

const STORAGE_KEY = "criba.voice.conversation";

type Country = "ES" | "MX";
const COUNTRIES: readonly Country[] = ["ES", "MX"];

/** A call is under way from connecting until it ends, fails or pauses (spec-voice-agent.web-proof WV9). Asking
 * for the microphone is not yet a call. While one is, the status region is the only live announcer. */
const DURING_CALL: ReadonlySet<VoiceState> = new Set(["connecting", "listening", "thinking", "speaking", "reconnecting"]);

/**
 * The Candidate's voice page (ADR 0059, owner decision 65). Voice is its own flow: the first tap on
 * "Hablar" starts a voice conversation (`channel: voice`) and a call on it. What each side says is read
 * back from the conversation's messages as captions; there is no chat input and no option buttons, since
 * options are spoken. No login: the conversation id is the bearer secret, kept in sessionStorage.
 */
export function Voice() {
  const { conversationId, messages, error, language, resume, reset } = useConversation({ storageKey: STORAGE_KEY });
  const [country, setCountry] = useState<Country>("MX");
  const log = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  const voice = useVoiceCall({
    conversationId,
    language,
    formCountry: country,
    ensureConversation: async () => {
      const conversation = await chatApi.start({ country, channel: "voice", client: "test_chat" });
      resume(conversation.conversation_id);
      return conversation.conversation_id;
    },
  });
  const copy: VoiceCopy = VOICE_COPY[voice.variant];
  const duringCall = DURING_CALL.has(voice.state);
  const shownCountry: Country = conversationId ? (voice.variant === "es-ES" ? "ES" : "MX") : country;

  // The 503 removes the control: focus, if it was there, goes to the heading instead of the page body.
  const wasAvailable = useRef(true);
  useLayoutEffect(() => {
    if (wasAvailable.current && !voice.available && document.activeElement === document.body) heading.current?.focus();
    wasAvailable.current = voice.available;
  }, [voice.available]);

  useEffect(() => {
    log.current?.scrollTo?.({ top: log.current.scrollHeight });
  }, [messages.length]);

  return (
    <main className="public voicecall" lang="es">
      <div className="row spread">
        <h1 ref={heading} tabIndex={-1}>
          {copy.title}
        </h1>
        <Link to="/applications">{copy.back}</Link>
      </div>
      {!conversationId && <p className="voicecall__intro">{copy.intro}</p>}

      <Card>
        <section className="voicecall__control" aria-label={copy.region}>
          {/* Fixed once the conversation exists: it shows the conversation's country until "Empezar de nuevo". */}
          <fieldset className="voicecall__countries" disabled={voice.live || conversationId !== null}>
            <legend>{copy.country}</legend>
            {COUNTRIES.map((c) => (
              <label key={c} className="voicecall__country">
                <input type="radio" name="voice-country" value={c} checked={shownCountry === c} onChange={() => setCountry(c)} />
                {copy.countries[c]}
              </label>
            ))}
          </fieldset>
          <p className="voicecall__notice">{copy.notice}</p>
          <div className="voicecall__row">
            {voice.available && (
              <Button
                variant={voice.live ? "danger" : "primary"}
                className="voicecall__toggle"
                aria-pressed={voice.live}
                onClick={() => (voice.live ? voice.stop() : voice.start())}
              >
                {voice.live ? copy.hangUp : copy.talk}
              </Button>
            )}
            {/* Static, never animated (WV11); hidden from assistive technology, as the status region says the same. */}
            {voice.state === "listening" && (
              <span className="voicecall__listening" aria-hidden="true">
                <Icon name="chat" />
                {voice.copy}
              </span>
            )}
          </div>
          <p className="voicecall__status" role="status">
            {voice.available ? voice.copy : copy.unavailable}
          </p>
        </section>
      </Card>

      {conversationId && (
        <Card>
          <section className="voicecall__transcript" aria-label={copy.captions}>
            <div className="voicecall__captions" ref={log} role="log" aria-live={duringCall ? "off" : "polite"} aria-label={copy.captions}>
              {messages.length === 0 && <p className="voicecall__empty">{copy.captionsEmpty}</p>}
              {messages.map((m) => (
                <Caption key={m.id} message={m} copy={copy} />
              ))}
            </div>
            {error && <p className="voicecall__lagging">{copy.captionsLagging}</p>}
            <Button
              className="voicecall__restart"
              onClick={() => {
                voice.reset();
                reset();
              }}
            >
              {copy.restart}
            </Button>
          </section>
        </Card>
      )}
    </main>
  );
}

/** One line of the transcript: who said it, then what. Options, links and attachments are never shown. */
function Caption({ message: m, copy }: { message: ChatMessage; copy: VoiceCopy }) {
  const who =
    m.sender === "system" ? null : m.direction === "inbound" ? copy.you : m.sender === "human" ? (m.sender_label ?? copy.team) : copy.agent;
  const side = m.sender === "system" ? "system" : m.direction === "inbound" ? "candidate" : "agent";
  return (
    <p className="voicecall__line" data-side={side}>
      {who && <span className="voicecall__who">{who}</span>}
      {m.text}
    </p>
  );
}
