// The voice call on /voice (spec-voice-agent.web-call WV5, WV8, WV10; ADR 0059): one state value, the
// gesture-bound tap handler, one reconnect retry (log decision 15), Esc, the page going to the
// background, navigation away and cleanup. The SDK is reached only through elevenlabsTransport.ts,
// loaded with import() after the session request.
//
// Tokens live only in locals of a start; the hook keeps no server message, no SDK text and no
// utterance text. Every agent end shows "ended" (WV7 is an owner decision, log decision 35).
import { useEffect, useState } from "react";
import { ApiError, chatApi } from "../api/client.ts";
import type { ChatLocale, VoiceSession } from "../api/types.ts";
import { VOICE_COPY, voiceVariant, type VoiceCopyState, type VoiceVariant } from "../lib/labels/voice.ts";
import type { TransportCall, TransportCallbacks, VoiceEndReason } from "./elevenlabsTransport.ts";

export type VoiceState = "idle" | VoiceCopyState;

const LIVE_STATES: readonly VoiceState[] = ["permission", "connecting", "listening", "thinking", "speaking", "reconnecting"];

/** A call is live from the tap until it ends, fails or pauses. */
export function isLive(state: VoiceState): boolean {
  return LIVE_STATES.includes(state);
}

export interface VoiceCallOptions {
  conversationId: string | null;
  /** The conversation's language, for the copy variant before a session exists. */
  language: ChatLocale | null;
  /** The start form's country, the last fallback for the copy variant. */
  formCountry: VoiceSession["country"];
  /** With no conversation yet, a tap starts one: called once the microphone is granted, it resolves to the
   * new conversation's id. Without it, a tap with no conversation does nothing. */
  ensureConversation?: () => Promise<string>;
}

export interface VoiceCall {
  state: VoiceState;
  /** The status region's text for the state; empty when idle. */
  copy: string;
  variant: VoiceVariant;
  /** False for the rest of the page's life once the backend answered 503 (WV6). */
  available: boolean;
  live: boolean;
  /** The tap: asks for the microphone before its first await, then mints and starts a session. */
  start: () => void;
  /** Hang up. */
  stop: () => void;
  /** A new conversation: end any call and clear the last call's status (back to idle). */
  reset: () => void;
}

/** One tap's call, across its one reconnect. */
interface Attempt {
  /** Null until `ensure` resolves, for a tap that starts the conversation too. */
  conversationId: string | null;
  ensure: (() => Promise<string>) | null;
  tracks: MediaStreamTrack[];
  context: AudioContext | null;
  call: TransportCall | null;
  /** Bumped per session started, so a dropped session's late callbacks are ignored. */
  leg: number;
  connected: boolean;
  retried: boolean;
  userPending: boolean;
}

interface Sink {
  state: (next: VoiceState | ((prev: VoiceState) => VoiceState)) => void;
  unavailable: () => void;
  country: (conversationId: string, country: VoiceSession["country"]) => void;
}

/** Only a refused or missing microphone is a denial (WV5); any other failure is connection lost. */
const MIC_DENIED = new Set(["NotAllowedError", "NotFoundError"]);

function refusal(status: number): VoiceState {
  if (status === 409 || status === 404) return "refused";
  if (status === 429) return "rateLimited";
  return "connectionLost";
}

class VoiceEngine {
  private current: Attempt | null = null;
  private available = true;
  private readonly sink: Sink;

  constructor(sink: Sink) {
    this.sink = sink;
  }

  start(conversationId: string | null, ensure?: () => Promise<string>): void {
    if (!this.available || (!conversationId && !ensure) || this.current) return;
    const attempt: Attempt = {
      conversationId,
      ensure: conversationId ? null : (ensure ?? null),
      tracks: [],
      context: null,
      call: null,
      leg: 0,
      connected: false,
      retried: false,
      userPending: false,
    };
    this.current = attempt;
    // Gesture-bound work, before the first await (WV8): the microphone, then one resumed audio context.
    let media: Promise<MediaStream>;
    try {
      media = navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      media = Promise.reject(err);
    }
    const Context = globalThis.AudioContext;
    if (Context) {
      try {
        attempt.context = new Context();
        attempt.context.resume().catch(() => undefined);
      } catch {
        // No audio: give the microphone back once it arrives, and let a later tap start again.
        media.then(
          (stream) => {
            for (const track of stream.getTracks()) track.stop();
          },
          () => undefined,
        );
        this.finish("connectionLost");
        return;
      }
    }
    this.sink.state("permission");
    void this.open(attempt, media);
  }

  stop(): void {
    this.finish("ended");
  }

  /** End any call and show nothing: the next conversation starts with no status line. */
  reset(): void {
    if (this.current) this.finish("idle");
    else this.sink.state("idle");
  }

  /** The page went to the background: end the call; a tap starts a new session. */
  pause(): void {
    this.finish("paused");
  }

  /** Unmount: end without touching state. */
  dispose(): void {
    const attempt = this.current;
    this.current = null;
    if (attempt) this.release(attempt);
  }

  private async open(attempt: Attempt, media: Promise<MediaStream>): Promise<void> {
    let stream: MediaStream;
    try {
      stream = await media;
    } catch (err) {
      const name = err instanceof DOMException || err instanceof Error ? err.name : "";
      if (this.current === attempt) this.finish(MIC_DENIED.has(name) ? "micDenied" : "connectionLost");
      return;
    }
    attempt.tracks = stream.getTracks();
    if (this.current !== attempt) {
      this.release(attempt);
      return;
    }
    this.sink.state("connecting");
    if (!attempt.conversationId && attempt.ensure) {
      try {
        attempt.conversationId = await attempt.ensure();
      } catch {
        if (this.current === attempt) this.finish("connectionLost");
        return;
      }
      if (this.current !== attempt) return;
    }
    await this.connect(attempt);
  }

  /** Mint a session and start it. The first leg maps refusals by status; the retry's every failure is connection lost. */
  private async connect(attempt: Attempt): Promise<void> {
    const retry = attempt.retried;
    const conversationId = attempt.conversationId;
    // Set before the first connect (by the tap or by `ensure`), so this never ends a call in practice.
    if (!conversationId) return this.finish("connectionLost");
    let session: VoiceSession;
    try {
      session = await chatApi.voiceSession(conversationId);
    } catch (err) {
      if (this.current !== attempt) return;
      const status = err instanceof ApiError ? err.status : 0;
      if (retry) {
        this.finish("connectionLost");
      } else if (status === 503) {
        this.available = false;
        this.sink.unavailable();
        this.finish("idle");
      } else {
        this.finish(refusal(status));
      }
      return;
    }
    if (this.current !== attempt) return;
    this.sink.country(conversationId, session.country);
    const leg = attempt.leg + 1;
    attempt.leg = leg;
    let call: TransportCall;
    try {
      const transport = await import("./elevenlabsTransport.ts");
      if (this.current !== attempt) return;
      call = await transport.start(session, this.callbacks(attempt, leg));
    } catch {
      if (this.current === attempt && attempt.leg === leg) this.finish("connectionLost");
      return;
    }
    if (this.current !== attempt || attempt.leg !== leg) {
      call.end().catch(() => undefined);
      return;
    }
    attempt.call = call;
    attempt.connected = true;
    // The SDK has the microphone now; the probe tracks of the tap are no longer needed.
    for (const track of attempt.tracks) track.stop();
    attempt.tracks = [];
    this.sink.state((prev) => (prev === "connecting" || prev === "reconnecting" ? "listening" : prev));
  }

  private callbacks(attempt: Attempt, leg: number): TransportCallbacks {
    const mine = () => this.current === attempt && attempt.leg === leg;
    const connected = () => {
      if (!mine()) return;
      attempt.connected = true;
      this.sink.state((prev) => (prev === "connecting" || prev === "reconnecting" ? "listening" : prev));
    };
    return {
      onConnect: connected,
      onStatusChange: (status) => {
        if (status === "connected") connected();
      },
      onModeChange: (mode) => {
        if (!mine()) return;
        if (mode === "speaking") {
          attempt.userPending = false;
          this.sink.state("speaking");
        } else {
          this.sink.state(attempt.userPending ? "thinking" : "listening");
        }
      },
      onUserUtterance: () => {
        if (!mine()) return;
        attempt.userPending = true;
        this.sink.state("thinking");
      },
      onError: () => undefined,
      onDisconnect: (reason: VoiceEndReason) => {
        if (!mine()) return;
        if (reason === "error" && attempt.connected && !attempt.retried) {
          this.reconnect(attempt);
          return;
        }
        this.finish(reason === "error" ? "connectionLost" : "ended");
      },
    };
  }

  /** One retry with a fresh session (log decision 15). */
  private reconnect(attempt: Attempt): void {
    attempt.retried = true;
    // Retire the old leg first: ending its call may report a synchronous "user" disconnect.
    attempt.leg += 1;
    attempt.connected = false;
    attempt.userPending = false;
    this.endCall(attempt);
    this.sink.state("reconnecting");
    void this.connect(attempt);
  }

  private finish(next: VoiceState): void {
    const attempt = this.current;
    if (!attempt) return;
    this.current = null;
    this.release(attempt);
    this.sink.state(next);
  }

  private endCall(attempt: Attempt): void {
    const call = attempt.call;
    attempt.call = null;
    if (call) call.end().catch(() => undefined);
  }

  private release(attempt: Attempt): void {
    this.endCall(attempt);
    for (const track of attempt.tracks) track.stop();
    attempt.tracks = [];
    const context = attempt.context;
    attempt.context = null;
    if (context) context.close().catch(() => undefined);
  }
}

export function useVoiceCall(options: VoiceCallOptions): VoiceCall {
  const [state, setState] = useState<VoiceState>("idle");
  const [available, setAvailable] = useState(true);
  const [session, setSession] = useState<{ conversationId: string; country: VoiceSession["country"] } | null>(null);
  const [engine] = useState(
    () =>
      new VoiceEngine({
        state: setState,
        unavailable: () => setAvailable(false),
        country: (conversationId, country) => setSession({ conversationId, country }),
      }),
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") engine.stop();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") engine.pause();
    };
    const onPageHide = () => engine.stop();
    document.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      engine.dispose();
    };
  }, [engine]);

  const variant = voiceVariant({
    sessionCountry: session && session.conversationId === options.conversationId ? session.country : null,
    language: options.language,
    formCountry: options.formCountry,
  });
  const { conversationId, ensureConversation } = options;
  return {
    state,
    copy: state === "idle" ? "" : VOICE_COPY[variant].states[state],
    variant,
    available,
    live: isLive(state),
    start: () => engine.start(conversationId, ensureConversation),
    stop: () => engine.stop(),
    reset: () => engine.reset(),
  };
}
