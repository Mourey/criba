// The only module that imports the ElevenLabs SDK (spec-voice-agent.web-call WV1, WV3). The page loads it
// with import() after the session request, so the SDK stays out of the entry chunk. It passes the SDK
// exactly three session fields plus callbacks, and hands the page no SDK text: a Candidate utterance is
// reported without its words, and an error without its message.
import { Conversation } from "@elevenlabs/react";
import type { VoiceSession } from "../api/types.ts";

export type VoiceMode = "speaking" | "listening";
export type VoiceStatus = "disconnected" | "connecting" | "connected" | "disconnecting";
export type VoiceEndReason = "user" | "agent" | "error";

export interface TransportCallbacks {
  onConnect: () => void;
  onDisconnect: (reason: VoiceEndReason) => void;
  onModeChange: (mode: VoiceMode) => void;
  onStatusChange: (status: VoiceStatus) => void;
  /** The Candidate said something; the words reach the chat through the long poll, not here. */
  onUserUtterance: () => void;
  /** The SDK reported an error. Its text is dropped; the disconnect that follows drives the state. */
  onError: () => void;
}

export interface TransportCall {
  end(): Promise<void>;
}

export async function start(session: VoiceSession, callbacks: TransportCallbacks): Promise<TransportCall> {
  const conversation = await Conversation.startSession({
    conversationToken: session.conversation_token,
    connectionType: "webrtc",
    customLlmExtraBody: { criba_voice_token: session.voice_token },
    onConnect: () => callbacks.onConnect(),
    onDisconnect: (details) => callbacks.onDisconnect(details.reason),
    onModeChange: ({ mode }) => callbacks.onModeChange(mode),
    onStatusChange: ({ status }) => callbacks.onStatusChange(status),
    onMessage: ({ source }) => {
      if (source === "user") callbacks.onUserUtterance();
    },
    onError: () => callbacks.onError(),
  });
  return { end: () => conversation.endSession() };
}
