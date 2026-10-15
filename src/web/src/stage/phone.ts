import type { ApplicationSummary, ChatLocale, ChatMessage } from "../api/types.ts";

// Pure helpers for the demo stage's phone and lookup. No React, no network.

export type PhoneCountry = "ES" | "MX";
export type TickState = "sent" | "delivered" | "read";

export const LOCALE_OF: Record<PhoneCountry, ChatLocale> = { ES: "es-ES", MX: "es-MX" };
export const TIME_ZONE_OF: Record<PhoneCountry, string> = { ES: "Europe/Madrid", MX: "America/Mexico_City" };

/** The contract's pattern for the phone a conversation may start with (contracts/webchat.openapi.yaml). */
const CONTRACT_PHONE = /^\+(34|52)[0-9]{8,11}$/;

const digit = (random: () => number, from: number, to: number): string => String(from + Math.floor(random() * (to - from + 1)));

/**
 * A fresh phone in the contract's format: +52 and 10 digits, or +34 and 9 digits starting with 6 or 7.
 * Always full length (the lookup matches on the whole number). The last 8 digits start with 1-9, so
 * the tail is never all zeros and the phone never equals a persona's +5255000000xx.
 */
export function generatePhone(country: PhoneCountry, random: () => number = Math.random): string {
  if (country === "MX") {
    // 10 digits: the first two, then 8 whose first digit is not 0.
    return `+52${digit(random, 1, 9)}${digit(random, 0, 9)}${digit(random, 1, 9)}${Array.from({ length: 7 }, () => digit(random, 0, 9)).join("")}`;
  }
  // 9 digits: 6 or 7, then 8 whose first digit is not 0.
  return `+34${digit(random, 6, 7)}${digit(random, 1, 9)}${Array.from({ length: 7 }, () => digit(random, 0, 9)).join("")}`;
}

/** The country a +34 or +52 phone belongs to, or null for anything else. */
export function countryOfPhone(phone: string): PhoneCountry | null {
  if (phone.startsWith("+52")) return "MX";
  if (phone.startsWith("+34")) return "ES";
  return null;
}

/** True when the phone matches the contract's pattern and, if a country is given, that country's prefix. */
export function isValidPhone(phone: string, country?: PhoneCountry): boolean {
  if (!CONTRACT_PHONE.test(phone)) return false;
  return country === undefined || countryOfPhone(phone) === country;
}

/**
 * The Application of this phone: strict string equality (no trimming, no normalising, no prefix
 * match), the open one if there are several, then the newest. Never another Candidate's row.
 */
export function matchByPhone(items: readonly ApplicationSummary[], phone: string): ApplicationSummary | null {
  const exact = items.filter((item) => item.candidate.phone === phone);
  if (exact.length === 0) return null;
  const rank = (item: ApplicationSummary) => (item.status === "open" ? 1 : 0);
  return [...exact].sort((a, b) => rank(b) - rank(a) || Date.parse(b.created_at) - Date.parse(a.created_at))[0];
}

/**
 * The tick of a Candidate's message, read from the thread (the backend reports no receipts): one tick
 * when it is stored; two when the agent's run is still going; two accented once a later agent or team
 * message exists.
 */
export function tickState(message: ChatMessage, messages: readonly ChatMessage[], runFinished: boolean | null): TickState {
  const answered = messages.some((m) => m.id > message.id && m.direction === "outbound" && (m.sender === "agent" || m.sender === "human"));
  if (answered) return "read";
  return runFinished === false ? "delivered" : "sent";
}

/** HH:mm of a message in the conversation country's time zone. */
export function bubbleTime(iso: string, country: PhoneCountry): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE_OF[country] }).format(date);
}

/** The calendar day of an instant in the country's zone, "2026-10-09". */
function dayInZone(date: Date, country: PhoneCountry): string {
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TIME_ZONE_OF[country] }).format(date);
}

/**
 * "09 Oct, 04:04": a date and time in the country's zone, so the record agrees with the bubbles. When
 * `today` (the clock) falls on the same day in that zone, the date is left out: "04:04".
 */
export function formatZoned(iso: string, country: PhoneCountry, today?: Date | null): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  if (today && dayInZone(date, country) === dayInZone(today, country)) return bubbleTime(iso, country);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: TIME_ZONE_OF[country],
  }).format(date);
}

/** "12 Oct": a date with no time (an answer such as the start date), in the same style as the clock. */
export function formatDateOnly(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }).format(date);
}

/** Up to two initials of a name, for the avatar. */
export function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase());
  return letters.join("");
}
