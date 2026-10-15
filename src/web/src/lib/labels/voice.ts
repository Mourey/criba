// Copy for the Candidate's voice page, /voice (ADR 0059, owner decision 65: voice is its own flow, not a
// mode of the chat). Terms follow CONTEXT.md. The page and the agent speak Spanish, so all of it is
// Spanish. The fixed strings (the notice, "Hablar", "Colgar", the refusal and "Toca para reanudar") are
// the same in both variants; the rest follows the country's Spanish. No line points to a chat, a link or
// typing: the page has none.
import type { ChatLocale, VoiceSession } from "../../api/types.ts";

export const surface = "Voice";

export type VoiceVariant = "es-ES" | "es-MX";

/** Every state of the voice call that shows copy in the status region (WV5). */
export const VOICE_STATES = [
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
] as const;

export type VoiceCopyState = (typeof VOICE_STATES)[number];

export interface VoiceCopy {
  /** The page heading and the voice region's name. */
  title: string;
  intro: string;
  notice: string;
  talk: string;
  hangUp: string;
  region: string;
  /** The country picker: its legend and the two choices. */
  country: string;
  countries: Record<VoiceSession["country"], string>;
  /** The transcript: its name, the hint before anything is said, and who said each line. */
  captions: string;
  captionsEmpty: string;
  you: string;
  agent: string;
  team: string;
  /** A transcript read that failed; the page keeps trying. */
  captionsLagging: string;
  /** Voice is off for this page (503): the control is gone and this line says why. */
  unavailable: string;
  restart: string;
  back: string;
  states: Record<VoiceCopyState, string>;
}

const NOTICE = "Hablarás con un asistente de IA. No grabamos el audio.";
const TALK = "Hablar";
const HANG_UP = "Colgar";
const REFUSED = "No podemos empezar la llamada ahora. Inténtalo más tarde.";
const PAUSED = "Toca para reanudar";
const REGION = "Llamada de voz";

const shared = {
  title: REGION,
  notice: NOTICE,
  talk: TALK,
  hangUp: HANG_UP,
  region: REGION,
  country: "País",
  countries: { ES: "España", MX: "México" },
  captions: "Transcripción",
  you: "Tú",
  agent: "Asistente",
  team: "Equipo",
  unavailable: "La llamada de voz no está disponible ahora. Inténtalo más tarde.",
  back: "Volver a la app",
} as const;

export const VOICE_COPY: Record<VoiceVariant, VoiceCopy> = {
  "es-ES": {
    ...shared,
    intro: "Elige tu país y pulsa Hablar.",
    captionsEmpty: "Aquí verás lo que se diga en la llamada.",
    captionsLagging: "La transcripción va con retraso. Seguimos intentándolo.",
    restart: "Empezar de nuevo",
    states: {
      permission: "Permite el acceso al micrófono para empezar.",
      connecting: "Conectando…",
      listening: "Te escucho.",
      thinking: "Un momento…",
      speaking: "Te está hablando el asistente.",
      reconnecting: "Se ha cortado. Volviendo a conectar…",
      ended: "La llamada ha terminado. Puedes volver a llamar cuando quieras.",
      micDenied: "No tenemos acceso al micrófono. Actívalo en los ajustes del navegador y vuelve a intentarlo.",
      rateLimited: "Has iniciado varias llamadas seguidas. Espera unos minutos y vuelve a intentarlo.",
      refused: REFUSED,
      connectionLost: "Se ha perdido la conexión. Vuelve a intentarlo.",
      paused: PAUSED,
    },
  },
  "es-MX": {
    ...shared,
    intro: "Elige tu país y toca Hablar.",
    captionsEmpty: "Aquí verás lo que se diga en la llamada.",
    captionsLagging: "La transcripción va con retraso. Seguimos intentando.",
    restart: "Empezar de nuevo",
    states: {
      permission: "Permite el acceso al micrófono para empezar.",
      connecting: "Conectando…",
      listening: "Te escucho.",
      thinking: "Un momento…",
      speaking: "Te está hablando el asistente.",
      reconnecting: "Se cortó. Volviendo a conectar…",
      ended: "La llamada terminó. Puedes volver a llamar cuando quieras.",
      micDenied: "No tenemos acceso al micrófono. Actívalo en la configuración del navegador y vuelve a intentarlo.",
      rateLimited: "Iniciaste varias llamadas seguidas. Espera unos minutos y vuelve a intentarlo.",
      refused: REFUSED,
      connectionLost: "Se perdió la conexión. Vuelve a intentarlo.",
      paused: PAUSED,
    },
  },
};

/**
 * The copy variant: the session's country once a session exists, then the conversation's language
 * (es-ES or es-MX), then the picked country.
 */
export function voiceVariant(input: {
  sessionCountry?: VoiceSession["country"] | null;
  language?: ChatLocale | null;
  formCountry: VoiceSession["country"];
}): VoiceVariant {
  if (input.sessionCountry) return input.sessionCountry === "ES" ? "es-ES" : "es-MX";
  if (input.language === "es-ES" || input.language === "es-MX") return input.language;
  return input.formCountry === "ES" ? "es-ES" : "es-MX";
}
