// The Takes of this tab: one conversation each, newest first, kept in sessionStorage so Stage, Recruiter,
// Stage survives a remount. A Take holds ids, the phone and the persona only: never message text, a
// name or an answer, and never the outcome, which is looked up by the exact phone each time it is shown.
// Every storage access is guarded: when storage throws on a read or a write, the list lives on in memory
// for the page session.

import type { ChatLocale } from "../api/types.ts";
import type { PhoneCountry } from "./phone.ts";

export const TAKES_KEY = "criba.stage.takes";
export const MAX_TAKES = 20;

export interface Take {
  conversationId: string;
  phone: string;
  personaId: string;
  country: PhoneCountry;
  locale: ChatLocale;
  startedAt: string;
}

let memory: Take[] = [];
// True after a write that storage refused: storage is then behind memory, so memory is the truth.
let storageBehind = false;

const isText = (value: unknown): value is string => typeof value === "string" && value !== "";

/** The six keys of a Take and nothing else, or null for a stored row that is not one. */
function toTake(row: unknown): Take | null {
  if (typeof row !== "object" || row === null) return null;
  const { conversationId, phone, personaId, country, locale, startedAt } = row as Record<string, unknown>;
  if (!isText(conversationId) || !isText(phone) || !isText(personaId) || !isText(locale) || !isText(startedAt)) return null;
  if (country !== "ES" && country !== "MX") return null;
  return { conversationId, phone, personaId, country, locale: locale as ChatLocale, startedAt };
}

function parse(raw: string | null): Take[] {
  if (raw === null) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(toTake).filter((row): row is Take => row !== null) : [];
  } catch {
    return [];
  }
}

/** The list, newest first. Storage when it can be read, else what this page session kept. */
export function readTakes(): Take[] {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(TAKES_KEY);
  } catch {
    // Storage is blocked: keep to the in-memory list.
    return memory;
  }
  if (storageBehind) return memory;
  memory = parse(raw).slice(0, MAX_TAKES);
  return memory;
}

/** Adds a Take at the front, replacing an earlier row of the same conversation, and keeps the newest 20. */
export function addTake(take: Take): Take[] {
  const { conversationId, phone, personaId, country, locale, startedAt } = take;
  const row: Take = { conversationId, phone, personaId, country, locale, startedAt };
  memory = [row, ...readTakes().filter((existing) => existing.conversationId !== conversationId)].slice(0, MAX_TAKES);
  try {
    window.sessionStorage.setItem(TAKES_KEY, JSON.stringify(memory));
    storageBehind = false;
  } catch {
    // Quota or blocked storage: the list stays in memory.
    storageBehind = true;
  }
  return memory;
}

/** Empties the list, in storage and in memory: Clear on the Presenter console. Client-side only. */
export function clearTakes(): Take[] {
  memory = [];
  try {
    window.sessionStorage.removeItem(TAKES_KEY);
    storageBehind = false;
  } catch {
    // Blocked storage: memory, now empty, is the truth for this page session.
    storageBehind = true;
  }
  return memory;
}
