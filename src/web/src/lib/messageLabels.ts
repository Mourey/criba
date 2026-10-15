import type { MessageLabel } from "../api/types.ts";
import * as copy from "./labels/recruiter.ts";

/** The value as a key of `table`, or null: only a known code is ever turned into words, never a raw value. */
function known<T extends Record<string, string>>(table: T, value: unknown): T[keyof T] | null {
  return typeof value === "string" && Object.hasOwn(table, value) ? table[value as keyof T] : null;
}

/**
 * Jev's reading of one typed message as words (ADR 0075), in a fixed order: the language, the signals that
 * fired, the intent, the tone hint and a language the message asked for or switched to. Codes from the server
 * become words through closed tables, so a number or an unknown code is never shown. The same for every role.
 */
export function messageLabelWords(label: MessageLabel): string[] {
  const words: (string | null)[] = [known(copy.labelsLanguage, label.language)];
  for (const s of label.fired) words.push(known(copy.labelsSignal, s));
  const intent = known(copy.labelsIntent, label.intent);
  if (intent) words.push(copy.labelsIntentLine(intent));
  const hint = known(copy.labelsHint, label.tone_hint);
  if (hint) words.push(copy.labelsHintLine(hint));
  const set = known(copy.labelsLanguageSet, label.language_set);
  if (set && label.language_reason === "request") words.push(copy.labelsAskedFor(set));
  if (set && label.language_reason === "switch") words.push(copy.labelsSwitchedTo(set));
  return words.filter((w): w is string => w !== null);
}
