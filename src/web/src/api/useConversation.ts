import { useCallback, useEffect, useRef, useState } from "react";
import { newMessageId } from "../lib/format.ts";
import { ApiError, chatApi } from "./client.ts";
import type { ChatLocale, ChatMessage, ChatStartRequest } from "./types.ts";

const LONG_POLL_SECONDS = 20;
/**
 * The hold after a page that brought a reply while its run was not over yet. The server returns as soon as the run
 * state changes during a poll, but if the run ended between two polls nothing changes during the next one: this
 * caps that wait.
 */
const SETTLE_POLL_SECONDS = 2;

function readStored(key: string | null): string | null {
  if (key === null) return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string | null, id: string | null): void {
  if (key === null) return;
  try {
    if (id) window.sessionStorage.setItem(key, id);
    else window.sessionStorage.removeItem(key);
  } catch {
    // Reconnecting after a reload is a convenience only.
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export interface UseConversationOptions {
  /** sessionStorage key that keeps the conversation id across a reload; `null` never touches storage. */
  storageKey: string | null;
}

/** What the web chat client holds for one Candidate conversation (contracts/webchat.openapi.yaml). */
export interface Conversation {
  conversationId: string | null;
  /** Ordered by id, without duplicates. */
  messages: ChatMessage[];
  /** The agent is typing, or its turn is not over (`agent_typing` or `run_finished === false`). */
  typing: boolean;
  /** The page's `run_finished`; `null` until the first page of a conversation. */
  runFinished: boolean | null;
  error: string | null;
  /** From the channel (optional in the contract): the agent's name and the conversation's language. */
  personaName: string | null;
  language: ChatLocale | null;
  /** True while `start` is waiting for the API. */
  busy: boolean;
  /** The one message id whose options are live (the latest interactive one, until the Candidate writes again). */
  answerableId: number | null;
  /** Candidate messages and taps the API has accepted in this session. */
  sent: number;
  start: (request: ChatStartRequest) => Promise<boolean>;
  resume: (conversationId: string) => void;
  /** True when the API accepted the message; false after setting `error`. */
  send: (text: string) => Promise<boolean>;
  /** True when the API accepted the tap; false after setting `error`. */
  tap: (message: ChatMessage, optionId: string) => Promise<boolean>;
  reset: () => void;
}

/**
 * The Candidate side of the web chat channel: no login, an unguessable conversation id, replies read by
 * long poll. Shared by /test-chat (which keeps the id in sessionStorage) and the demo stage (which does
 * not). All traffic goes through `chatApi`.
 */
export function useConversation({ storageKey }: UseConversationOptions): Conversation {
  const [conversationId, setConversationId] = useState<string | null>(() => readStored(storageKey));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [typing, setTyping] = useState(false);
  const [runFinished, setRunFinished] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [personaName, setPersonaName] = useState<string | null>(null);
  const [language, setLanguage] = useState<ChatLocale | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(0);
  // Read position survives a wake-up, so sending a message re-reads at once instead of waiting.
  const cursorRef = useRef(0);
  const seenRef = useRef(new Set<number>());
  const [wake, setWake] = useState(0);
  // The running long poll. A send aborts it first: a browser opens at most six HTTP/1.1 connections to a host, and
  // other windows' long polls and event streams can hold the rest, so the send would otherwise queue behind them.
  const pollRef = useRef<AbortController | null>(null);

  // Long-poll loop: read after the cursor, hold up to LONG_POLL_SECONDS for a newer message.
  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    pollRef.current = controller;
    const seen = seenRef.current;
    let last: { typing: boolean; finished: boolean | undefined } | null = null;
    let settling = false;

    (async () => {
      while (!controller.signal.aborted) {
        const startedAt = Date.now();
        try {
          const wait = settling ? SETTLE_POLL_SECONDS : LONG_POLL_SECONDS;
          const page = await chatApi.read(conversationId, cursorRef.current, wait, controller.signal, last);
          if (controller.signal.aborted) return;
          // `run_finished` (contract) is false from the moment a message that starts a run is
          // accepted until its reply is stored; `agent_typing` only covers the run itself, so a
          // queued run would otherwise show nothing.
          setTyping(page.agent_typing || page.run_finished === false);
          setRunFinished(page.run_finished);
          if (page.persona_name !== undefined) setPersonaName(page.persona_name);
          if (page.language !== undefined) setLanguage(page.language);
          const fresh = page.messages.filter((m) => !seen.has(m.id));
          for (const m of fresh) seen.add(m.id);
          cursorRef.current = Math.max(cursorRef.current, page.cursor);
          // A changed run state is news even without a message: the server returns on it.
          const changed = last !== null && (last.typing !== page.agent_typing || last.finished !== page.run_finished);
          last = { typing: page.agent_typing, finished: page.run_finished };
          settling = page.run_finished === false && fresh.some((m) => m.direction === "outbound");
          if (fresh.length > 0) {
            setMessages((prev) => [...prev, ...fresh].sort((a, b) => a.id - b.id));
            setError(null);
          } else if (!changed && Date.now() - startedAt < 1000) {
            // The server returned at once with nothing new (no long poll); do not spin.
            await sleep(1500, controller.signal);
          }
        } catch (e) {
          if (controller.signal.aborted) return;
          if (e instanceof ApiError && e.status === 404) {
            writeStored(storageKey, null);
            setConversationId(null);
            setError("That conversation no longer exists. Start a new one.");
            return;
          }
          setError(e instanceof Error ? e.message : "Lost the connection. Retrying.");
          await sleep(3000, controller.signal);
        }
      }
    })();

    return () => {
      controller.abort();
      if (pollRef.current === controller) pollRef.current = null;
    };
  }, [conversationId, wake, storageKey]);

  const forget = useCallback(() => {
    cursorRef.current = 0;
    seenRef.current = new Set();
    setMessages([]);
    setPersonaName(null);
    setLanguage(null);
    setTyping(false);
    setRunFinished(null);
    setSent(0);
  }, []);

  const start = useCallback(
    async (request: ChatStartRequest): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        const conversation = await chatApi.start(request);
        writeStored(storageKey, conversation.conversation_id);
        forget();
        setPersonaName(conversation.persona_name ?? null);
        setLanguage(conversation.language ?? null);
        setConversationId(conversation.conversation_id);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not start the conversation.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [forget, storageKey],
  );

  const resume = useCallback(
    (id: string) => {
      writeStored(storageKey, id);
      forget();
      setError(null);
      setConversationId(id);
      // The same id again does not rerun the effect by itself; a wake does, and it reads from 0.
      setWake((n) => n + 1);
    },
    [forget, storageKey],
  );

  const reset = useCallback(() => {
    writeStored(storageKey, null);
    forget();
    setConversationId(null);
    setError(null);
  }, [forget, storageKey]);

  // The last send that failed. Sending the same text again reuses its message_id: if the first
  // attempt was stored and only its answer was lost, the server answers `duplicate: true` instead
  // of running the agent on the same text twice (contracts/webchat.openapi.yaml, idempotent on
  // message_id).
  const unsentRef = useRef<{ conversationId: string; text: string; messageId: string } | null>(null);

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (!conversationId) return false;
      const unsent = unsentRef.current;
      const messageId =
        unsent && unsent.conversationId === conversationId && unsent.text === text ? unsent.messageId : newMessageId();
      unsentRef.current = { conversationId, text, messageId };
      pollRef.current?.abort();
      try {
        await chatApi.send(conversationId, messageId, text);
        unsentRef.current = null;
        setError(null);
        setSent((n) => n + 1);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not send the message.");
        return false;
      } finally {
        // The poll was stopped for the send: start it again either way.
        setWake((n) => n + 1);
      }
    },
    [conversationId],
  );

  const tap = useCallback(
    async (message: ChatMessage, optionId: string): Promise<boolean> => {
      if (!conversationId) return false;
      pollRef.current?.abort();
      try {
        await chatApi.reply(conversationId, newMessageId(), message.id, optionId);
        setError(null);
        setSent((n) => n + 1);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not send the reply.");
        return false;
      } finally {
        setWake((n) => n + 1);
      }
    },
    [conversationId],
  );

  // Only the latest interactive message is answerable, and only until the Candidate writes again.
  const lastInteractive = [...messages].reverse().find((m) => m.interactive);
  const answerableId =
    lastInteractive && !messages.some((m) => m.id > lastInteractive.id && m.direction === "inbound") ? lastInteractive.id : null;

  return {
    conversationId,
    messages,
    typing,
    runFinished,
    error,
    personaName,
    language,
    busy,
    answerableId,
    sent,
    start,
    resume,
    send,
    tap,
    reset,
  };
}
