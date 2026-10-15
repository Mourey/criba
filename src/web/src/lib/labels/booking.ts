// Copy for the Candidate's public booking page (/book/:token). Terms follow CONTEXT.md.
// The page speaks the Candidate's language: Spanish for es-ES and es-MX, English for en.
// Phase 4 adds names here without editing labels.ts.
import { ApiError } from "../../api/client.ts";
import type { Language } from "../../api/types.ts";

export const surface = "Booking";

export type PageLanguage = "es" | "en";

/** The language of the page's copy; Spanish until the first load says otherwise. */
export function pageLanguage(language: Language | undefined): PageLanguage {
  return language === "en" ? "en" : "es";
}

export const COPY = {
  es: {
    title: "Elige el horario de tu entrevista",
    hello: (name: string | null) => (name ? `Hola, ${name}.` : "Hola."),
    pick: "Selecciona un horario y confirma.",
    confirm: "Confirmar entrevista",
    none: "Ahora mismo no hay horarios disponibles. Escríbenos por WhatsApp y te ayudamos.",
    current: "Ya tienes una entrevista:",
    change: "Si eliges otro horario, cambiaremos tu entrevista.",
    download: "Añadir a mi calendario (.ics)",
    validUntil: (date: string) => `Este enlace es válido hasta el ${date}.`,
    loading: "Cargando…",
    pickFirst: "Elige un horario para continuar.",
    chosen: (when: string, where: string) => `Tu horario: ${when}, ${where}.`,
    doneTitle: "Entrevista confirmada",
    taken: (when: string | null, where: string | null) => `Ese horario se acaba de ocupar${when ? ` (${when})` : ""}${where ? ` en ${where}` : ""}. Elige otro.`,
    generic: "No pudimos completar la solicitud. Inténtalo de nuevo o escríbenos por WhatsApp.",
    invalid: "Este enlace no es válido. Escríbenos por WhatsApp y te ayudamos.",
    gone: "Este enlace ya caducó. Escríbenos por WhatsApp y te enviamos uno nuevo.",
  },
  en: {
    title: "Choose your interview time",
    hello: (name: string | null) => (name ? `Hi ${name}.` : "Hi."),
    pick: "Select a time and confirm.",
    confirm: "Confirm interview",
    none: "There are no times available right now. Message us on WhatsApp and we will help.",
    current: "You already have an interview:",
    change: "If you pick another time, your interview will be moved.",
    download: "Add to my calendar (.ics)",
    validUntil: (date: string) => `This link is valid until ${date}.`,
    loading: "Loading…",
    pickFirst: "Choose a time to continue.",
    chosen: (when: string, where: string) => `Your time: ${when}, ${where}.`,
    doneTitle: "Interview confirmed",
    taken: (when: string | null, where: string | null) => `That time was just taken${when ? ` (${when})` : ""}${where ? ` at ${where}` : ""}. Pick another.`,
    generic: "We could not complete that. Please try again or message us on WhatsApp.",
    invalid: "This link is not valid. Message us on WhatsApp and we will help.",
    gone: "This link is no longer active. Message us on WhatsApp and we will send a new one.",
  },
} as const;

/** Before the first load the Candidate's language is unknown, so a failed load speaks Spanish. */
export const LOAD_FAILED = "No pudimos cargar esta página.";
export const LOAD_RETRY = "Reintentar";

/** Headings and tab titles where the language is unknown: a link that cannot be used, and one no longer active. */
export const LINK_TITLE = { es: "Enlace no disponible", en: "Link not available" } as const;
export const GONE_TITLE = { es: "Este enlace ya caducó", en: "This link is no longer active" } as const;
/** A page that could not load speaks Spanish: the Candidate's language is not known yet. */
export const LOAD_FAILED_TITLE = "Algo salió mal";

/** A slot's label split at its first " · " into the day and the time; no separator means no day. */
export function splitLabel(label: string): { day: string; time: string } {
  const at = label.indexOf(" · ");
  return at === -1 ? { day: "", time: label } : { day: label.slice(0, at), time: label.slice(at + 3) };
}

export type FailureKind = "invalid" | "gone" | "taken" | "retryable" | "generic";

/**
 * What went wrong, from the status and code of an ApiError or from a request that never arrived.
 * The page shows its own copy for each kind and never the error's message, except the server's text
 * for "gone", which is already in the Candidate's language.
 */
export function failureKind(error: unknown): FailureKind {
  if (error instanceof ApiError) {
    if (error.status === 404) return "invalid";
    if (error.status === 410) return "gone";
    if (error.status === 409 && error.code === "slot_taken") return "taken";
    return error.status >= 500 ? "retryable" : "generic";
  }
  return error instanceof TypeError ? "retryable" : "generic";
}

const LOCALES: Record<Language, string> = { "es-ES": "es-ES", "es-MX": "es-MX", en: "en" };

/** The link's last day as a long date in the Candidate's language, or null when the value is not a date. */
export function formatExpiry(iso: string, language: Language): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(LOCALES[language], { dateStyle: "long" }).format(date);
}

/**
 * The month of a day heading ("octubre"). The label carries no month and the contract no time zone, but a
 * Location's offset from UTC is under a day, so the slot's local date is the UTC date of starts_at or the day
 * before or after it. The one of those three whose day of the month is the number in the label's day part
 * gives the month, formatted in UTC: the browser's time zone plays no part. Null when none matches.
 */
export function dayMonth(startsAt: string, day: string, language: Language): string | null {
  const start = new Date(startsAt).getTime();
  const number = /\d+/.exec(day);
  if (Number.isNaN(start) || !number) return null;
  for (const shift of [0, -1, 1]) {
    const date = new Date(start + shift * 86_400_000);
    if (date.getUTCDate() === Number(number[0])) {
      return new Intl.DateTimeFormat(LOCALES[language], { month: "long", timeZone: "UTC" }).format(date);
    }
  }
  return null;
}
