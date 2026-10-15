// The Candidate's voice page, /voice (ADR 0059, owner decision 65): the voice copy, the call hook and its
// transport, and the page. The hook and copy tests moved here from testchat.test.tsx when voice left
// /test-chat (decision 65); the page tests replace the /test-chat voice tests.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { afterEach, beforeEach, vi, type Mock } from "vitest";
import { chatApi as client } from "../../src/api/client.ts";
import { Icon } from "../../src/components/ui/Icon.tsx";
import type { ChatLocale, ChatMessage, VoiceSession } from "../../src/api/types.ts";
import { VOICE_COPY, VOICE_STATES, voiceVariant } from "../../src/lib/labels/voice.ts";
import type { TransportCallbacks } from "../../src/voice/elevenlabsTransport.ts";
import { useVoiceCall } from "../../src/voice/useVoiceCall.ts";
import { mockApi, renderRoute } from "./helpers.tsx";

// The SDK is never loaded in a test. The transport is mocked file-wide; the transport's own test loads
// the real module with vi.importActual against the mocked SDK.
const sdk = vi.hoisted(() => ({ startSession: vi.fn() }));
vi.mock("@elevenlabs/react", () => ({ Conversation: { startSession: sdk.startSession } }));
const transport = vi.hoisted(() => ({ start: vi.fn() }));
vi.mock("../../src/voice/elevenlabsTransport.ts", () => ({ start: transport.start }));

const CONV = "wv_9f2c7e1b4a6d4e0f8b3a5c7d9e1f2a3b";

function msg(partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "direction" | "sender" | "text">): ChatMessage {
  return {
    message_id: null,
    sender_label: null,
    interactive: null,
    reply_to: null,
    links: [],
    attachments: [],
    created_at: "2026-10-09T10:00:10Z",
    ...partial,
  };
}

const VOICE_PATH = `/api/chat/conversations/${CONV}/voice-session`;
// Low-entropy fixture tokens: words, never real credentials.
const ES_SESSION = {
  conversation_token: "conversation-token-word",
  voice_token: "voice-token-word",
  expires_at: "2026-10-11T10:30:00Z",
  country: "ES",
} satisfies VoiceSession;

describe("voice call", () => {
  it("voiceSession posts to the voice-session path with no body", async () => {
    const mock = mockApi({ "POST /api/chat/conversations/:id/voice-session": [201, ES_SESSION] });
    const session = await client.voiceSession(CONV);
    expect(session).toEqual(ES_SESSION);
    const calls = mock.find("POST", VOICE_PATH);
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toBeUndefined();
  });

  it("voice copy has es-ES and es-MX variants with the fixed strings identical", () => {
    const es = VOICE_COPY["es-ES"];
    const mx = VOICE_COPY["es-MX"];
    const notice = "Hablarás con un asistente de IA. No grabamos el audio.";
    for (const v of [es, mx]) {
      expect(v.notice).toBe(notice);
      expect(v.talk).toBe("Hablar");
      expect(v.hangUp).toBe("Colgar");
      expect(v.states.refused).toBe("No podemos empezar la llamada ahora. Inténtalo más tarde.");
      expect(v.states.paused).toBe("Toca para reanudar");
    }
    expect(VOICE_STATES).toEqual(
      expect.arrayContaining([
        "permission",
        "connecting",
        "listening",
        "thinking",
        "speaking",
        "reconnecting",
        "ended",
        "micDenied",
        "rateLimited",
        "refused",
        "connectionLost",
        "paused",
      ]),
    );
    for (const state of VOICE_STATES) {
      expect(es.states[state].trim()).not.toBe("");
      expect(mx.states[state].trim()).not.toBe("");
    }
  });

  it("voice copy uses no avoided term and no English", () => {
    const strings = (["es-ES", "es-MX"] as const).flatMap((k) => {
      const v = VOICE_COPY[k];
      return [v.notice, v.talk, v.hangUp, v.region, ...Object.values(v.states)];
    });
    const all = strings.join(" ").toLowerCase();
    for (const avoided of ["handoff", "admin", "case record", "disqualif", "appeal", "applicant"]) {
      expect(all).not.toContain(avoided);
    }
    for (const english of ["call", "connecting", "microphone", "error"]) {
      expect(all).not.toMatch(new RegExp(`\\b${english}\\b`));
    }
  });

  it("picks the variant from the session country, then the conversation language, then the form country", () => {
    expect(voiceVariant({ sessionCountry: "MX", language: "es-ES", formCountry: "ES" })).toBe("es-MX");
    expect(voiceVariant({ sessionCountry: "ES", language: "es-MX", formCountry: "MX" })).toBe("es-ES");
    expect(voiceVariant({ language: "es-ES", formCountry: "MX" })).toBe("es-ES");
    expect(voiceVariant({ language: "es-MX", formCountry: "ES" })).toBe("es-MX");
    expect(voiceVariant({ language: "en", formCountry: "ES" })).toBe("es-ES");
    expect(voiceVariant({ language: null, formCountry: "MX" })).toBe("es-MX");
  });
});

// --- Voice call, the hook and the transport (WV3, WV5, WV8, WV10) ---

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface FakeTrack {
  stop: Mock<() => void>;
}

interface VoiceEnv {
  order: string[];
  tracks: FakeTrack[];
  contexts: { resume: Mock<() => Promise<void>>; close: Mock<() => Promise<void>> }[];
  getUserMedia: Mock<(c: MediaStreamConstraints) => Promise<{ getTracks: () => FakeTrack[] }>>;
  grant: () => void;
}

/** Stub the microphone and the audio context. `deny` rejects getUserMedia; `hold` keeps it pending until grant(). */
function voiceEnv(options: { deny?: string; hold?: boolean } = {}): VoiceEnv {
  const order: string[] = [];
  const tracks: FakeTrack[] = [];
  const contexts: VoiceEnv["contexts"] = [];
  const held = deferred<void>();
  if (!options.hold) held.resolve();
  const getUserMedia = vi.fn((constraints: MediaStreamConstraints) => {
    expect(constraints).toEqual({ audio: true });
    order.push("getUserMedia");
    if (options.deny) return Promise.reject(new DOMException("blocked", options.deny));
    const mine = [{ stop: vi.fn() }, { stop: vi.fn() }];
    tracks.push(...mine);
    return held.promise.then(() => ({ getTracks: () => mine }));
  });
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  class FakeAudioContext {
    resume = vi.fn(() => {
      order.push("resume");
      return Promise.resolve();
    });
    close = vi.fn(() => Promise.resolve());
    constructor() {
      order.push("AudioContext");
      contexts.push(this);
    }
  }
  vi.stubGlobal("AudioContext", FakeAudioContext);
  return { order, tracks, contexts, getUserMedia, grant: () => held.resolve() };
}

interface FakeCall {
  session: VoiceSession;
  cb: TransportCallbacks;
  end: Mock<() => Promise<void>>;
}

/** The mocked transport records each start; `fail` makes the next starts reject with SDK text. */
function fakeTransport(options: { fail?: boolean } = {}): FakeCall[] {
  const calls: FakeCall[] = [];
  transport.start.mockImplementation(async (session: VoiceSession, cb: TransportCallbacks) => {
    if (options.fail) throw new Error("PLANTED-SDK-TEXT");
    const end = vi.fn(async () => undefined);
    calls.push({ session, cb, end });
    return { end };
  });
  return calls;
}

const PLANTED = "PLANTED-SERVER-TEXT";

/** The voice-session endpoint: answers each request from `replies` in turn, then the last one again. */
function voiceApi(order: string[], replies: unknown[] = [[201, ES_SESSION]]) {
  let n = 0;
  return mockApi({
    "POST /api/chat/conversations/:id/voice-session": () => {
      order.push("session");
      const reply = replies[Math.min(n, replies.length - 1)];
      n += 1;
      if (typeof reply === "function") return (reply as () => unknown)() as object;
      return reply as object;
    },
  });
}

type HookProps = { conversationId: string | null; language: ChatLocale | null; formCountry: "ES" | "MX" };

function renderVoice(props: HookProps = { conversationId: CONV, language: null, formCountry: "ES" }) {
  const hook = renderHook((p: HookProps) => useVoiceCall(p), { initialProps: props });
  const tapper = render(
    <button type="button" onClick={() => hook.result.current.start()}>
      tap
    </button>,
  );
  return {
    ...hook,
    tap: () => fireEvent.click(within(tapper.container).getByRole("button", { name: "tap" })),
    done: () => {
      hook.unmount();
      tapper.unmount();
    },
  };
}

const ES = VOICE_COPY["es-ES"].states;

function setVisibility(value: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", { value, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("voice call", () => {
  beforeEach(() => {
    transport.start.mockReset();
    sdk.startSession.mockReset();
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    Reflect.deleteProperty(document, "visibilityState");
    vi.restoreAllMocks();
  });

  it("passes exactly three session fields to the SDK", async () => {
    const real = await vi.importActual<typeof import("../../src/voice/elevenlabsTransport.ts")>("../../src/voice/elevenlabsTransport.ts");
    const endSession = vi.fn(async () => undefined);
    sdk.startSession.mockResolvedValue({ endSession });
    const cb = {
      onConnect: vi.fn(),
      onDisconnect: vi.fn(),
      onModeChange: vi.fn(),
      onStatusChange: vi.fn(),
      onUserUtterance: vi.fn(),
      onError: vi.fn(),
    };
    const call = await real.start(ES_SESSION, cb);

    expect(sdk.startSession).toHaveBeenCalledTimes(1);
    const options = sdk.startSession.mock.calls[0][0] as Record<string, unknown>;
    expect(Object.keys(options).filter((k) => !/^on[A-Z]/.test(k)).sort()).toEqual([
      "connectionType",
      "conversationToken",
      "customLlmExtraBody",
    ]);
    expect(options.conversationToken).toBe(ES_SESSION.conversation_token);
    expect(options.connectionType).toBe("webrtc");
    expect(options.customLlmExtraBody).toEqual({ criba_voice_token: ES_SESSION.voice_token });
    for (const forbidden of ["overrides", "dynamicVariables", "agentId", "signedUrl", "textOnly", "clientTools", "userId"]) {
      expect(options).not.toHaveProperty(forbidden);
    }

    const sdkCallbacks = options as {
      onConnect: (p: { conversationId: string }) => void;
      onDisconnect: (d: { reason: string; message?: string }) => void;
      onModeChange: (p: { mode: string }) => void;
      onStatusChange: (p: { status: string }) => void;
      onMessage: (p: { message: string; source: string; role: string }) => void;
      onError: (message: string) => void;
    };
    sdkCallbacks.onConnect({ conversationId: "conv-word" });
    sdkCallbacks.onStatusChange({ status: "connected" });
    sdkCallbacks.onModeChange({ mode: "speaking" });
    sdkCallbacks.onMessage({ message: "hola", source: "user", role: "user" });
    sdkCallbacks.onMessage({ message: "buenas", source: "ai", role: "agent" });
    sdkCallbacks.onError(PLANTED);
    sdkCallbacks.onDisconnect({ reason: "error", message: PLANTED });
    expect(cb.onConnect).toHaveBeenCalledWith();
    expect(cb.onStatusChange).toHaveBeenCalledWith("connected");
    expect(cb.onModeChange).toHaveBeenCalledWith("speaking");
    expect(cb.onUserUtterance).toHaveBeenCalledTimes(1);
    expect(cb.onUserUtterance).toHaveBeenCalledWith();
    expect(cb.onError).toHaveBeenCalledWith();
    expect(cb.onDisconnect).toHaveBeenCalledWith("error");

    await call.end();
    expect(endSession).toHaveBeenCalledTimes(1);
  });

  it("asks for the microphone synchronously in the tap, before the session request", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    voiceApi(env.order);
    const v = renderVoice();

    v.tap();
    // Still inside the same task: no microtask has run since the click.
    expect(env.getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(env.order).toEqual(["getUserMedia", "AudioContext", "resume"]);
    expect(v.result.current.state).toBe("permission");

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(env.order.indexOf("session")).toBeGreaterThan(env.order.indexOf("resume"));
    v.done();
  });

  it.each(["NotAllowedError", "NotFoundError"])("a denied microphone sends no session request (%s)", async (name) => {
    const env = voiceEnv({ deny: name });
    fakeTransport();
    const mock = voiceApi(env.order);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("micDenied"));
    expect(v.result.current.copy).toBe(ES.micDenied);
    expect(mock.calls).toHaveLength(0);
    expect(transport.start).not.toHaveBeenCalled();
    expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
    v.done();
  });

  it("stops the probe tracks once the session starts", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    voiceApi(env.order);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    expect(env.tracks.length).toBeGreaterThan(0);
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    expect(calls[0].end).not.toHaveBeenCalled();
    v.done();
  });

  it("walks every state with its copy", async () => {
    const env = voiceEnv({ hold: true });
    const calls = fakeTransport();
    const session = deferred<unknown>();
    voiceApi(env.order, [() => session.promise, [201, ES_SESSION]]);
    const v = renderVoice();
    const expectState = (state: keyof typeof ES) => {
      expect(v.result.current.state).toBe(state);
      expect(v.result.current.copy).toBe(ES[state]);
    };

    expect(v.result.current.state).toBe("idle");
    expect(v.result.current.copy).toBe("");
    v.tap();
    expectState("permission");
    env.grant();
    await waitFor(() => expectState("connecting"));
    session.resolve([201, ES_SESSION]);
    await waitFor(() => expect(calls).toHaveLength(1));
    const { cb } = calls[0];
    await waitFor(() => expectState("listening"));
    act(() => cb.onStatusChange("connected"));
    act(() => cb.onConnect());
    expectState("listening");
    act(() => cb.onUserUtterance());
    expectState("thinking");
    act(() => cb.onModeChange("listening"));
    expectState("thinking");
    act(() => cb.onModeChange("speaking"));
    expectState("speaking");
    act(() => cb.onModeChange("listening"));
    expectState("listening");
    act(() => cb.onDisconnect("agent"));
    expectState("ended");
    expect(calls[0].end).toHaveBeenCalledTimes(1);

    v.tap();
    await waitFor(() => expect(calls).toHaveLength(2));
    act(() => calls[1].cb.onConnect());
    act(() => calls[1].cb.onDisconnect("user"));
    expectState("ended");
    expect(calls[1].end).toHaveBeenCalledTimes(1);

    v.tap();
    await waitFor(() => expect(calls).toHaveLength(3));
    act(() => calls[2].cb.onConnect());
    act(() => setVisibility("hidden"));
    expectState("paused");
    expect(calls[2].end).toHaveBeenCalledTimes(1);
    v.done();
  });

  it.each([
    [409, "refused"],
    [404, "refused"],
    [429, "rateLimited"],
    [422, "connectionLost"],
    [500, "connectionLost"],
    ["network", "connectionLost"],
    ["sdk", "connectionLost"],
  ] as const)("maps refusals by status and never keeps the server message (%s)", async (status, state) => {
    const env = voiceEnv();
    fakeTransport({ fail: status === "sdk" });
    const reply =
      status === "network"
        ? () => {
            throw new TypeError(PLANTED);
          }
        : status === "sdk"
          ? [201, ES_SESSION]
          : [status, { error: "voice_refused", message: PLANTED }];
    voiceApi(env.order, [reply]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe(state));
    expect(v.result.current.copy).toBe(ES[state]);
    expect(v.result.current.available).toBe(true);
    const returned = JSON.stringify(v.result.current);
    expect(returned).not.toContain(PLANTED);
    expect(returned).not.toContain("PLANTED-SDK-TEXT");
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    v.done();
  });

  it("a 503 makes voice unavailable for the rest of the hook's life", async () => {
    const env = voiceEnv();
    fakeTransport();
    const mock = voiceApi(env.order, [[503, { error: "voice_unavailable", message: PLANTED }]]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(v.result.current.available).toBe(false));
    expect(v.result.current.state).toBe("idle");
    expect(v.result.current.copy).toBe("");
    v.tap();
    expect(mock.calls).toHaveLength(1);
    v.rerender({ conversationId: "wc_other", language: null, formCountry: "ES" });
    expect(v.result.current.available).toBe(false);
    v.done();
  });

  it("an error disconnect reconnects once with a fresh session", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order, [
      [201, ES_SESSION],
      [201, { ...ES_SESSION, conversation_token: "second-conversation-word", voice_token: "second-voice-word" }],
    ]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => calls[0].cb.onDisconnect("error"));
    expect(v.result.current.state).toBe("reconnecting");
    expect(v.result.current.copy).toBe(ES.reconnecting);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].session.voice_token).toBe("second-voice-word");
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    expect(mock.calls).toHaveLength(2);
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    expect(env.getUserMedia).toHaveBeenCalledTimes(1);
    v.done();
  });

  it("a second error disconnect or a failed retry shows connection lost with no third request", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order, [[201, ES_SESSION]]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => calls[0].cb.onDisconnect("error"));
    await waitFor(() => expect(calls).toHaveLength(2));
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    act(() => calls[1].cb.onDisconnect("error"));
    expect(v.result.current.state).toBe("connectionLost");
    expect(v.result.current.copy).toBe(ES.connectionLost);
    await act(async () => undefined);
    expect(mock.calls).toHaveLength(2);
    expect(calls[1].end).toHaveBeenCalledTimes(1);
    v.done();

    const env2 = voiceEnv();
    const calls2 = fakeTransport();
    const mock2 = voiceApi(env2.order, [[201, ES_SESSION], [500, { error: "internal", message: PLANTED }]]);
    const w = renderVoice();
    w.tap();
    await waitFor(() => expect(calls2).toHaveLength(1));
    act(() => calls2[0].cb.onConnect());
    act(() => calls2[0].cb.onDisconnect("error"));
    await waitFor(() => expect(w.result.current.state).toBe("connectionLost"));
    expect(mock2.calls).toHaveLength(2);
    expect(calls2).toHaveLength(1);
    w.done();
  });

  it("the page going to the background ends the call and pauses", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => setVisibility("hidden"));
    expect(v.result.current.state).toBe("paused");
    expect(v.result.current.copy).toBe("Toca para reanudar");
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    act(() => setVisibility("visible"));
    expect(v.result.current.state).toBe("paused");

    v.tap();
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(mock.calls).toHaveLength(2);
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    v.done();
  });

  it("going to the background while the microphone is pending pauses and mints nothing", async () => {
    const env = voiceEnv({ hold: true });
    fakeTransport();
    const mock = voiceApi(env.order);
    const v = renderVoice();
    v.tap();
    act(() => setVisibility("hidden"));
    expect(v.result.current.state).toBe("paused");
    env.grant();
    await act(async () => undefined);
    expect(mock.calls).toHaveLength(0);
    expect(v.result.current.state).toBe("paused");
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    v.done();
  });

  it.each(["stop", "escape", "agent", "error", "pagehide", "unmount"] as const)(
    "every hook end path calls end once and stops every track (%s)",
    async (path) => {
      const env = voiceEnv();
      const calls = fakeTransport();
      voiceApi(env.order, [[201, ES_SESSION], [500, { error: "internal", message: PLANTED }]]);
      const v = renderVoice();
      v.tap();
      await waitFor(() => expect(calls).toHaveLength(1));
      act(() => calls[0].cb.onConnect());

      if (path === "stop") act(() => v.result.current.stop());
      if (path === "escape") fireEvent.keyDown(document, { key: "Escape" });
      if (path === "agent") act(() => calls[0].cb.onDisconnect("agent"));
      if (path === "error") {
        act(() => calls[0].cb.onDisconnect("error"));
        await waitFor(() => expect(v.result.current.state).toBe("connectionLost"));
      }
      if (path === "pagehide") act(() => void window.dispatchEvent(new Event("pagehide")));
      if (path === "unmount") v.done();

      expect(calls[0].end).toHaveBeenCalledTimes(1);
      for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
      expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
      if (path !== "unmount") {
        expect(v.result.current.live).toBe(false);
        v.done();
      }
      expect(calls[0].end).toHaveBeenCalledTimes(1);
    },
  );

  it("stopping while the session request is pending starts no call", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const session = deferred<unknown>();
    voiceApi(env.order, [() => session.promise]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("connecting"));
    act(() => v.result.current.stop());
    expect(v.result.current.state).toBe("ended");
    session.resolve([201, ES_SESSION]);
    await act(async () => undefined);
    expect(transport.start).not.toHaveBeenCalled();
    expect(calls).toHaveLength(0);
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
    expect(v.result.current.state).toBe("ended");
    v.done();
  });

  it("an audio context that fails to start releases the microphone, shows connection lost and lets a later tap start", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order);
    const Working = globalThis.AudioContext;
    let constructed = 0;
    vi.stubGlobal(
      "AudioContext",
      class extends Working {
        constructor() {
          constructed += 1;
          if (constructed === 1) throw new DOMException("no audio", "NotSupportedError");
          super();
        }
      },
    );
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("connectionLost"));
    expect(v.result.current.copy).toBe(ES.connectionLost);
    await act(async () => undefined);
    expect(env.tracks).toHaveLength(2);
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    expect(mock.calls).toHaveLength(0);

    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    expect(mock.calls).toHaveLength(1);
    v.done();
  });

  it("a reconnect ignores the old leg's user disconnect fired while it ends", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order, [[201, ES_SESSION]]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    // The SDK reports its own end() as a "user" disconnect, synchronously.
    calls[0].end.mockImplementation(async () => calls[0].cb.onDisconnect("user"));
    act(() => calls[0].cb.onDisconnect("error"));
    expect(v.result.current.state).toBe("reconnecting");
    await waitFor(() => expect(calls).toHaveLength(2));
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    expect(mock.calls).toHaveLength(2);
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    v.done();
  });

  it.each(["SecurityError", "NotReadableError", "AbortError"])(
    "a microphone failure that is not a denial shows connection lost (%s)",
    async (name) => {
      const env = voiceEnv({ deny: name });
      fakeTransport();
      const mock = voiceApi(env.order);
      const v = renderVoice();
      v.tap();
      await waitFor(() => expect(v.result.current.state).toBe("connectionLost"));
      expect(v.result.current.copy).toBe(ES.connectionLost);
      expect(mock.calls).toHaveLength(0);
      expect(transport.start).not.toHaveBeenCalled();
      expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
      v.done();
    },
  );

  it("stopping while the SDK session is starting ends that call once it starts", async () => {
    const env = voiceEnv();
    const started = deferred<void>();
    const end = vi.fn(async () => undefined);
    transport.start.mockImplementation(async () => {
      await started.promise;
      return { end };
    });
    voiceApi(env.order);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(transport.start).toHaveBeenCalledTimes(1));
    expect(v.result.current.state).toBe("connecting");
    act(() => v.result.current.stop());
    expect(v.result.current.state).toBe("ended");
    started.resolve();
    await act(async () => undefined);
    expect(end).toHaveBeenCalledTimes(1);
    expect(v.result.current.state).toBe("ended");
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
    v.done();
  });

  it("stopping during a reconnect mints nothing more and starts no second call", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const retry = deferred<unknown>();
    const mock = voiceApi(env.order, [[201, ES_SESSION], () => retry.promise]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => calls[0].cb.onDisconnect("error"));
    expect(v.result.current.state).toBe("reconnecting");
    await waitFor(() => expect(mock.calls).toHaveLength(2));
    act(() => v.result.current.stop());
    expect(v.result.current.state).toBe("ended");
    retry.resolve([201, ES_SESSION]);
    await act(async () => undefined);
    expect(calls).toHaveLength(1);
    expect(mock.calls).toHaveLength(2);
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
    expect(v.result.current.state).toBe("ended");
    v.done();
  });

  it("no token reaches console, storage or the URL", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const storage = [vi.spyOn(Storage.prototype, "setItem")];
    const history = [vi.spyOn(window.history, "pushState"), vi.spyOn(window.history, "replaceState")];
    const env = voiceEnv();
    const calls = fakeTransport();
    voiceApi(env.order, [[201, ES_SESSION], [201, ES_SESSION], [409, { error: "voice_refused", message: PLANTED }]]);
    const v = renderVoice();
    v.tap();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => calls[0].cb.onDisconnect("error"));
    await waitFor(() => expect(calls).toHaveLength(2));
    act(() => (calls[1].cb.onError as (message: string) => void)(PLANTED));
    act(() => v.result.current.stop());
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("refused"));

    const tokens = [ES_SESSION.conversation_token, ES_SESSION.voice_token];
    const seen = JSON.stringify([...spies, ...storage, ...history].map((s) => s.mock.calls));
    for (const token of tokens) {
      expect(seen).not.toContain(token);
      expect(window.location.href).not.toContain(token);
      expect(JSON.stringify(v.result.current)).not.toContain(token);
      expect(JSON.stringify({ ...window.localStorage })).not.toContain(token);
      expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(token);
    }
    expect(JSON.stringify(v.result.current)).not.toContain(PLANTED);
    v.done();
  });
});

// --- A tap that starts the voice conversation too (ensureConversation) ---

describe("voice call: a tap with no conversation yet", () => {
  beforeEach(() => {
    transport.start.mockReset();
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    vi.restoreAllMocks();
  });

  function renderEnsure(ensure: () => Promise<string>) {
    const hook = renderHook(() => useVoiceCall({ conversationId: null, language: null, formCountry: "ES", ensureConversation: ensure }));
    const tapper = render(
      <button type="button" onClick={() => hook.result.current.start()}>
        tap
      </button>,
    );
    return {
      ...hook,
      tap: () => fireEvent.click(within(tapper.container).getByRole("button", { name: "tap" })),
      done: () => {
        hook.unmount();
        tapper.unmount();
      },
    };
  }

  it("asks for the microphone first, then starts the conversation, then mints a session on its id", async () => {
    const env = voiceEnv();
    const calls = fakeTransport();
    const mock = voiceApi(env.order);
    const ensure = vi.fn(async () => {
      env.order.push("ensure");
      return CONV;
    });
    const v = renderEnsure(ensure);
    v.tap();
    expect(env.order).toEqual(["getUserMedia", "AudioContext", "resume"]);
    expect(ensure).not.toHaveBeenCalled();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(env.order).toEqual(["getUserMedia", "AudioContext", "resume", "ensure", "session"]);
    expect(ensure).toHaveBeenCalledTimes(1);
    expect(mock.calls[0].path).toBe(VOICE_PATH);
    await waitFor(() => expect(v.result.current.state).toBe("listening"));
    v.done();
  });

  it("a denied microphone starts no conversation", async () => {
    const env = voiceEnv({ deny: "NotAllowedError" });
    fakeTransport();
    const mock = voiceApi(env.order);
    const ensure = vi.fn(async () => CONV);
    const v = renderEnsure(ensure);
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("micDenied"));
    expect(ensure).not.toHaveBeenCalled();
    expect(mock.calls).toHaveLength(0);
    v.done();
  });

  it("a conversation that fails to start shows connection lost, mints nothing and releases the microphone", async () => {
    const env = voiceEnv();
    fakeTransport();
    const mock = voiceApi(env.order);
    const v = renderEnsure(async () => {
      throw new Error(PLANTED);
    });
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("connectionLost"));
    expect(v.result.current.copy).toBe(ES.connectionLost);
    expect(JSON.stringify(v.result.current)).not.toContain(PLANTED);
    expect(mock.calls).toHaveLength(0);
    for (const track of env.tracks) expect(track.stop).toHaveBeenCalled();
    expect(env.contexts[0].close).toHaveBeenCalledTimes(1);
    v.done();
  });

  it("hanging up while the conversation is starting mints nothing", async () => {
    const env = voiceEnv();
    fakeTransport();
    const mock = voiceApi(env.order);
    const started = deferred<string>();
    const v = renderEnsure(() => started.promise);
    v.tap();
    await waitFor(() => expect(v.result.current.state).toBe("connecting"));
    act(() => v.result.current.stop());
    expect(v.result.current.state).toBe("ended");
    started.resolve(CONV);
    await act(async () => undefined);
    expect(mock.calls).toHaveLength(0);
    expect(transport.start).not.toHaveBeenCalled();
    v.done();
  });

  it("with neither a conversation nor a way to start one, a tap does nothing", () => {
    const env = voiceEnv();
    const hook = renderHook(() => useVoiceCall({ conversationId: null, language: null, formCountry: "MX" }));
    act(() => hook.result.current.start());
    expect(env.getUserMedia).not.toHaveBeenCalled();
    expect(hook.result.current.state).toBe("idle");
    hook.unmount();
  });
});

// --- The page (ADR 0059, decision 65; WV4, WV5's status region, WV6, WV9, WV11, WV12, WV14) ---

const MX_SESSION = { ...ES_SESSION, country: "MX" } satisfies VoiceSession;
const STORAGE = "criba.voice.conversation";
const START_PATH = "/api/chat/conversations";

const disclosure = msg({ id: 1, direction: "outbound", sender: "agent", sender_label: "Sazón Empleo", text: "Hola, soy un asistente de IA." });
const said = msg({ id: 2, direction: "inbound", sender: "candidate", text: "Hola, me llamo Ana" });
const spokenOptions = msg({
  id: 3,
  direction: "outbound",
  sender: "agent",
  sender_label: "Sazón Empleo",
  text: "¿Tienes carnet de conducir? Puedes decir sí o no.",
  interactive: { style: "buttons", options: [{ id: "yes", label: "Sí" }, { id: "no", label: "No" }] },
  links: [{ label: "Reservar entrevista", url: "/book/abc" }],
});

/**
 * The page. `stored` puts a conversation in sessionStorage first (a reload), otherwise the first tap starts
 * one. The voice endpoint answers `replies` in turn; GET returns whatever is in `store`.
 */
function voicePage(options: { replies?: unknown[]; store?: ChatMessage[]; stored?: boolean; extra?: Record<string, unknown> } = {}) {
  const { replies = [[201, ES_SESSION]], store = [], stored = false, extra = {} } = options;
  const env = voiceEnv();
  const calls = fakeTransport();
  let n = 0;
  const mock = mockApi({
    "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, channel: "voice" }],
    "GET /api/chat/conversations/:id/messages": (call) => {
      const after = Number(call.query.get("after") ?? 0);
      return { cursor: store.at(-1)?.id ?? 0, agent_typing: false, run_finished: true, messages: store.filter((m) => m.id > after) };
    },
    "POST /api/chat/conversations/:id/voice-session": () => {
      const reply = replies[Math.min(n, replies.length - 1)];
      n += 1;
      return reply as object;
    },
    ...extra,
  });
  if (stored) window.sessionStorage.setItem(STORAGE, CONV);
  renderRoute("/voice");
  const voiceRequests = () => mock.find("POST", VOICE_PATH);
  return { env, calls, mock, store, voiceRequests };
}

const COPY = VOICE_COPY["es-MX"];
const MX = COPY.states;
const statusText = () => within(screen.getByRole("region", { name: COPY.region })).getByRole("status").textContent;
const captions = () => screen.getByRole("log", { name: COPY.captions });
const logLive = () => captions().getAttribute("aria-live");
/** The long poll re-reads within its 1.5 s idle wait, so a line stored mid-call needs a longer wait. */
const POLL = { timeout: 4000 };

/** Tap Hablar and bring the call to listening. Returns the call's index in `calls`. */
async function connectCall(calls: FakeCall[]): Promise<number> {
  const before = calls.length;
  fireEvent.click(await screen.findByRole("button", { name: COPY.talk }));
  await waitFor(() => expect(calls).toHaveLength(before + 1));
  act(() => calls[before].cb.onConnect());
  await waitFor(() => expect(statusText()).toBe(MX.listening));
  return before;
}

describe("voice page", () => {
  beforeEach(() => {
    transport.start.mockReset();
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    Reflect.deleteProperty(document, "visibilityState");
    vi.restoreAllMocks();
  });

  it("shows the country choice, the notice and one control before the first tap, in Spanish, with no login and no request", async () => {
    const { mock } = voicePage();
    expect(await screen.findByRole("heading", { name: COPY.title })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("lang", "es");
    expect(screen.getByText("Hablarás con un asistente de IA. No grabamos el audio.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "México" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "España" })).not.toBeChecked();
    expect(screen.getAllByRole("button", { name: /^(Hablar|Colgar)$/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Hablar" })).toHaveAttribute("aria-pressed", "false");
    // No chat: no message field, no send button, no transcript until there is a conversation.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send|enviar/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
    expect(mock.calls).toHaveLength(0);
  });

  it("the first tap starts a voice conversation for the picked country and a call on it", async () => {
    const { calls, mock } = voicePage({ replies: [[201, ES_SESSION]] });
    fireEvent.click(await screen.findByRole("radio", { name: "España" }));
    fireEvent.click(screen.getByRole("button", { name: "Hablar" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(mock.find("POST", START_PATH)).toHaveLength(1);
    expect(mock.find("POST", START_PATH)[0].body).toEqual({ country: "ES", channel: "voice", client: "test_chat" });
    expect(mock.find("POST", VOICE_PATH)).toHaveLength(1);
    const startAt = mock.calls.findIndex((c) => c.path === START_PATH);
    const sessionAt = mock.calls.findIndex((c) => c.path === VOICE_PATH);
    expect(startAt).toBeLessThan(sessionAt);
    expect(window.sessionStorage.getItem(STORAGE)).toBe(CONV);
    // The country is fixed for this conversation.
    expect(screen.getByRole("radio", { name: "España" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "España" })).toBeDisabled();
    expect(screen.getByRole("log", { name: VOICE_COPY["es-ES"].captions })).toBeInTheDocument();
  });

  it("a second call reuses the conversation", async () => {
    const { calls, mock } = voicePage({ replies: [[201, MX_SESSION]] });
    await connectCall(calls);
    fireEvent.click(screen.getByRole("button", { name: COPY.hangUp }));
    await connectCall(calls);
    expect(mock.find("POST", START_PATH)).toHaveLength(1);
    expect(mock.find("POST", VOICE_PATH)).toHaveLength(2);
  });

  it("captions show what each side said, newest at the bottom, with no option buttons and no links", async () => {
    const store: ChatMessage[] = [];
    const { calls } = voicePage({ replies: [[201, MX_SESSION]], store });
    await connectCall(calls);
    expect(within(captions()).getByText(COPY.captionsEmpty)).toBeInTheDocument();
    store.push(disclosure, said, spokenOptions);
    await within(captions()).findByText(spokenOptions.text, undefined, POLL);
    const lines = [...captions().querySelectorAll(".voicecall__line")];
    expect(lines.map((l) => l.getAttribute("data-side"))).toEqual(["agent", "candidate", "agent"]);
    expect(lines[0]).toHaveTextContent(`${COPY.agent}${disclosure.text}`);
    expect(lines[1]).toHaveTextContent(`${COPY.you}${said.text}`);
    expect(lines[2]).toHaveTextContent(spokenOptions.text);
    expect(within(captions()).queryAllByRole("button")).toHaveLength(0);
    expect(within(captions()).queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Sí" })).not.toBeInTheDocument();
    expect(screen.queryByText(COPY.captionsEmpty)).not.toBeInTheDocument();
  });

  it("a team member's line carries the team's label and a system line none", async () => {
    voicePage({
      stored: true,
      store: [
        msg({ id: 5, direction: "outbound", sender: "human", sender_label: "Equipo de Grupo Sazón", text: "Hola, soy del equipo." }),
        msg({ id: 6, direction: "outbound", sender: "human", text: "Seguimos en contacto." }),
        msg({ id: 7, direction: "outbound", sender: "system", text: "Llamada terminada" }),
      ],
    });
    const log = await screen.findByRole("log", { name: COPY.captions });
    expect(await within(log).findByText("Equipo de Grupo Sazón")).toBeInTheDocument();
    expect(within(log).getByText(COPY.team)).toBeInTheDocument();
    const system = within(log).getByText("Llamada terminada");
    expect(system).toHaveAttribute("data-side", "system");
    expect(system.querySelector(".voicecall__who")).toBeNull();
  });

  it("the control toggles aria-pressed and its name, Esc ends, and focus stays on the control", async () => {
    const { calls } = voicePage({ replies: [[201, MX_SESSION], [201, MX_SESSION], [500, { error: "internal", message: PLANTED }]] });
    const control = await screen.findByRole("button", { name: "Hablar" });
    control.focus();
    fireEvent.click(control);
    expect(control).toHaveAttribute("aria-pressed", "true");
    expect(control).toHaveAccessibleName("Colgar");
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    act(() => calls[0].cb.onModeChange("speaking"));
    expect(document.activeElement).toBe(control);

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    expect(control).toHaveAttribute("aria-pressed", "false");
    expect(control).toHaveAccessibleName("Hablar");
    expect(statusText()).toBe(MX.ended);
    expect(document.activeElement).toBe(control);

    fireEvent.click(control);
    await waitFor(() => expect(calls).toHaveLength(2));
    act(() => calls[1].cb.onConnect());
    act(() => calls[1].cb.onDisconnect("error"));
    await waitFor(() => expect(statusText()).toBe(MX.connectionLost));
    expect(document.activeElement).toBe(control);
  });

  it("the status region shows each state's copy, the 409 as the generic line, and no server or SDK text", async () => {
    const { calls } = voicePage({ replies: [[201, MX_SESSION], [409, { error: "voice_refused", message: PLANTED }]] });
    fireEvent.click(await screen.findByRole("button", { name: "Hablar" }));
    expect(statusText()).toBe(MX.permission);
    await waitFor(() => expect(calls).toHaveLength(1));
    await waitFor(() => expect(statusText()).toBe(MX.listening));
    act(() => calls[0].cb.onUserUtterance());
    expect(statusText()).toBe(MX.thinking);
    act(() => calls[0].cb.onModeChange("speaking"));
    expect(statusText()).toBe(MX.speaking);
    act(() => (calls[0].cb.onError as (message: string) => void)(PLANTED));
    act(() => calls[0].cb.onDisconnect("agent"));
    expect(statusText()).toBe(MX.ended);

    fireEvent.click(screen.getByRole("button", { name: "Hablar" }));
    await waitFor(() => expect(statusText()).toBe("No podemos empezar la llamada ahora. Inténtalo más tarde."));
    expect(screen.getByRole("button", { name: "Hablar" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(PLANTED);
    expect(document.body.innerHTML).not.toContain(PLANTED);
  });

  it("503 hides the control, says voice is unavailable, moves focus to the heading and never retries", async () => {
    const { voiceRequests } = voicePage({ replies: [[503, { error: "voice_unavailable", message: PLANTED }]] });
    const control = await screen.findByRole("button", { name: "Hablar" });
    control.focus();
    fireEvent.click(control);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Hablar" })).not.toBeInTheDocument());
    expect(statusText()).toBe(COPY.unavailable);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: COPY.title }));
    expect(document.body.textContent).not.toContain(PLANTED);
    await act(async () => undefined);
    expect(voiceRequests()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: COPY.restart }));
    expect(await screen.findByRole("radio", { name: "México" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Hablar" })).not.toBeInTheDocument();
    expect(voiceRequests()).toHaveLength(1);
  });

  it("Colgar keeps the conversation; Empezar de nuevo ends the call and starts over", async () => {
    const { calls } = voicePage({ replies: [[201, MX_SESSION]] });
    await connectCall(calls);
    fireEvent.click(screen.getByRole("button", { name: "Colgar" }));
    expect(calls[0].end).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(STORAGE)).toBe(CONV);
    expect(captions()).toBeInTheDocument();

    await connectCall(calls);
    fireEvent.click(screen.getByRole("button", { name: COPY.restart }));
    expect(calls[1].end).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(STORAGE)).toBeNull();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "México" })).toBeEnabled();
    expect(statusText()).toBe("");
    expect(screen.getByRole("button", { name: "Hablar" })).toHaveAttribute("aria-pressed", "false");
  });

  it("a stored conversation comes back after a reload, with its captions and its country", async () => {
    const { mock } = voicePage({
      stored: true,
      extra: {
        "GET /api/chat/conversations/:id/messages": () => ({ cursor: 1, agent_typing: false, run_finished: true, language: "es-ES", messages: [disclosure] }),
      },
    });
    const log = await screen.findByRole("log", { name: VOICE_COPY["es-ES"].captions });
    expect(await within(log).findByText(disclosure.text)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("radio", { name: "España" })).toBeChecked());
    expect(screen.getByRole("radio", { name: "España" })).toBeDisabled();
    expect(mock.find("POST", START_PATH)).toHaveLength(0);
  });

  it("a failed transcript read shows the lagging line and keeps the call", async () => {
    let fail = false;
    const { calls } = voicePage({
      replies: [[201, MX_SESSION]],
      extra: {
        "GET /api/chat/conversations/:id/messages": () =>
          fail ? [500, { error: "internal", message: PLANTED }] : { cursor: 0, agent_typing: false, run_finished: true, messages: [] },
      },
    });
    await connectCall(calls);
    fail = true;
    expect(await screen.findByText(COPY.captionsLagging, undefined, POLL)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(PLANTED);
    expect(screen.getByRole("button", { name: "Colgar" })).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    ["ES", ES_SESSION, "es-ES"],
    ["MX", MX_SESSION, "es-MX"],
  ] as const)("shows the ES or MX variant (%s)", async (country, session, variant) => {
    const { calls } = voicePage({ replies: [[201, session]] });
    fireEvent.click(await screen.findByRole("radio", { name: country === "ES" ? "España" : "México" }));
    fireEvent.click(screen.getByRole("button", { name: "Hablar" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    fireEvent.click(screen.getByRole("button", { name: "Colgar" }));
    const status = within(screen.getByRole("region", { name: VOICE_COPY[variant].region })).getByRole("status");
    expect(status).toHaveTextContent(VOICE_COPY[variant].states.ended);
    expect(VOICE_COPY["es-ES"].states.ended).not.toBe(VOICE_COPY["es-MX"].states.ended);
  });

  it("no token reaches the DOM", async () => {
    const { calls } = voicePage({ replies: [[201, MX_SESSION]] });
    const tokens = [ES_SESSION.conversation_token, ES_SESSION.voice_token];
    const html = () => document.documentElement.outerHTML;
    await connectCall(calls);
    act(() => calls[0].cb.onModeChange("speaking"));
    for (const token of tokens) expect(html()).not.toContain(token);
    fireEvent.click(screen.getByRole("button", { name: "Colgar" }));
    for (const token of tokens) expect(html()).not.toContain(token);
  });
});

describe("voice page: the captions during a call", () => {
  beforeEach(() => {
    transport.start.mockReset();
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    Reflect.deleteProperty(document, "visibilityState");
    vi.restoreAllMocks();
  });

  it("the captions are aria-live off during a call and polite after", async () => {
    const session = deferred<void>();
    const { calls } = voicePage({ stored: true, replies: [session.promise.then(() => [201, MX_SESSION]), [201, MX_SESSION]] });
    await screen.findByRole("log", { name: COPY.captions });
    expect(logLive()).toBe("polite");
    const env = voiceEnv({ hold: true });

    // Asking for the microphone is not yet a call.
    fireEvent.click(screen.getByRole("button", { name: "Hablar" }));
    expect(statusText()).toBe(MX.permission);
    expect(logLive()).toBe("polite");
    env.grant();
    await waitFor(() => expect(statusText()).toBe(MX.connecting));
    expect(logLive()).toBe("off");
    session.resolve();
    await waitFor(() => expect(calls).toHaveLength(1));
    act(() => calls[0].cb.onConnect());
    expect(statusText()).toBe(MX.listening);
    expect(logLive()).toBe("off");
    act(() => calls[0].cb.onUserUtterance());
    expect(logLive()).toBe("off");
    act(() => calls[0].cb.onModeChange("speaking"));
    expect(logLive()).toBe("off");
    act(() => calls[0].cb.onDisconnect("error"));
    expect(statusText()).toBe(MX.reconnecting);
    expect(logLive()).toBe("off");
    await waitFor(() => expect(calls).toHaveLength(2));
    act(() => calls[1].cb.onConnect());
    act(() => calls[1].cb.onDisconnect("agent"));
    expect(statusText()).toBe(MX.ended);
    expect(logLive()).toBe("polite");

    await connectCall(calls);
    expect(logLive()).toBe("off");
    act(() => setVisibility("hidden"));
    expect(statusText()).toBe(MX.paused);
    expect(logLive()).toBe("polite");
  });

  it("the status region changes only with the state, never with a caption", async () => {
    const store: ChatMessage[] = [];
    const { calls } = voicePage({ replies: [[201, MX_SESSION]], store });
    const call = await connectCall(calls);
    const spoken = "Te escucho: dime tu código postal, por favor.";
    store.push(msg({ id: 15, direction: "outbound", sender: "agent", sender_label: "Sazón Empleo", text: spoken }));
    expect(await within(captions()).findByText(spoken, undefined, POLL)).toBeInTheDocument();
    expect(statusText()).toBe(MX.listening);
    expect(statusText()).not.toContain(spoken);
    act(() => calls[call].cb.onModeChange("speaking"));
    expect(statusText()).toBe(MX.speaking);
  });

  it("new captions scroll the transcript to the bottom", async () => {
    const original = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTo");
    const scroll = vi.fn();
    Object.defineProperty(Element.prototype, "scrollTo", { value: scroll, configurable: true, writable: true });
    try {
      const store: ChatMessage[] = [];
      const { calls } = voicePage({ replies: [[201, MX_SESSION]], store });
      await connectCall(calls);
      const before = scroll.mock.calls.length;
      store.push(disclosure);
      await within(captions()).findByText(disclosure.text, undefined, POLL);
      await waitFor(() => expect(scroll.mock.calls.length).toBeGreaterThan(before));
      expect(scroll.mock.contexts.at(-1)).toBe(captions());
    } finally {
      if (original) Object.defineProperty(Element.prototype, "scrollTo", original);
      else Reflect.deleteProperty(Element.prototype, "scrollTo");
    }
  });
});

describe("voice page: the listening indicator", () => {
  beforeEach(() => {
    transport.start.mockReset();
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    vi.restoreAllMocks();
  });

  const region = () => screen.getByRole("region", { name: COPY.region });
  const indicator = () => region().querySelector(".voicecall__listening");

  it("the listening indicator shows a static icon and its text, outside the status region", async () => {
    const glyph = render(<Icon name="chat" />).container.querySelector("svg")?.outerHTML;
    expect(glyph).toBeTruthy();
    const { calls } = voicePage({ replies: [[201, MX_SESSION]] });
    await screen.findByRole("button", { name: "Hablar" });
    expect(indicator()).toBeNull();

    const call = await connectCall(calls);
    const shown = indicator();
    expect(shown).not.toBeNull();
    expect(shown).toHaveAttribute("aria-hidden", "true");
    expect(shown?.textContent).toBe(MX.listening);
    expect(shown?.querySelector("svg")?.outerHTML).toBe(glyph);
    expect(within(region()).getByRole("status").contains(shown)).toBe(false);

    act(() => calls[call].cb.onUserUtterance());
    expect(indicator()).toBeNull();
    act(() => calls[call].cb.onModeChange("speaking"));
    expect(indicator()).toBeNull();
    act(() => calls[call].cb.onModeChange("listening"));
    expect(indicator()).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Colgar" }));
    expect(statusText()).toBe(MX.ended);
    expect(indicator()).toBeNull();
  });

  it("the voice page's styles never animate", () => {
    const css = readFileSync(join(__dirname, "../../src/styles/surfaces/voice.css"), "utf8");
    const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim(), body: m[2] }));
    expect(rules.some((r) => r.selector.includes(".voicecall__listening"))).toBe(true);
    expect(css).not.toContain("@keyframes");
    const reduced = rules.find((r) => r.selector.includes(".voicecall *"));
    expect(reduced?.body).toMatch(/animation:\s*none/);
    expect(reduced?.body).toMatch(/transition:\s*none/);
    for (const rule of rules) {
      if (rule === reduced) continue;
      expect(rule.body, rule.selector).not.toMatch(/(^|[\s;])(animation|transition)(-[a-z-]+)?\s*:/);
    }
  });

  it("no line of the voice copy points to a chat, typing, a link or a button", () => {
    const all = (["es-ES", "es-MX"] as const)
      .flatMap((k) => {
        const v = VOICE_COPY[k];
        return [v.title, v.intro, v.notice, v.talk, v.hangUp, v.region, v.captions, v.captionsEmpty, v.captionsLagging, v.unavailable, v.restart, ...Object.values(v.states)];
      })
      .join(" ")
      .toLowerCase();
    for (const pointer of ["chat", "escrib", "enlace", "botón", "boton", "por aquí"]) expect(all).not.toContain(pointer);
  });
});
