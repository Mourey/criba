// A line-by-line port of src/tests/sim/detect.py (`detect_prompt` and what it uses): which question
// does an agent message ask? The Demo player answers with the persona's n-th reply to that
// question, exactly as the Python driver does. Change a pattern here only with the same change in
// detect.py; tests/unit/detect.test.ts checks every label in tests/fixtures/detect-corpus.json
// against the Python detector.
//
// Python's `.` matches anything but "\n" (written `[^\n]` below); JavaScript's also skips "\r" and the
// Unicode line separators.
//
// Python's `\b`, `\w` and `\s` are Unicode-aware on str patterns; JavaScript's `\b` and `\w` are
// ASCII only. `py()` rewrites them as explicit Unicode classes, so a letter such as "ß" or "ł"
// (which normalisation leaves alone) is a word character here, as it is in Python.

export type PromptKind =
  | "full_name"
  | "zone"
  | "driver_license"
  | "availability"
  | "preferred_schedule"
  | "experience_years"
  | "experience_platforms"
  | "start_date"
  | "confirm";

export const PROMPT_KINDS: readonly PromptKind[] = [
  "full_name",
  "zone",
  "driver_license",
  "availability",
  "preferred_schedule",
  "experience_years",
  "experience_platforms",
  "start_date",
  "confirm",
];

// The part of an `interactive` block detection reads. A full message block (style, labels) fits.
export type Interactive = { options?: ReadonlyArray<{ id?: string; label?: string }> };

const WORD = "[\\p{L}\\p{N}_]";
// Python's `\s` for str: str.isspace().
const SPACE = "[\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const BOUNDARY = `(?:(?<=${WORD})(?!${WORD})|(?<!${WORD})(?=${WORD}))`;

function py(source: string, flags = ""): RegExp {
  const unicode = source.replace(/\\([bws])/g, (_, c: string) => (c === "b" ? BOUNDARY : c === "w" ? WORD : SPACE));
  return new RegExp(unicode, `u${flags}`);
}

const AVAILABILITY_IDS = new Set(["full_time", "part_time", "weekends"]);
const SCHEDULE_IDS = new Set(["morning", "afternoon", "evening", "flexible"]);
// The platforms list ends with the "no app" row (ADR 0053).
const PLATFORM_IDS = new Set(["glovo", "uber_eats", "just_eat", "rappi", "didi_food", "other", "none"]);
// A start-date choice offers the dates themselves (ADR 0054). Python's `$` also matches before a
// final newline.
const DATE_ID = /^[0-9]{4}-[0-9]{2}-[0-9]{2}\n?$/;

// Checked in this order against the last question of the message, accents stripped.
// More specific topics come first ("platforms" before "experience", "confirm" before all).
export const PROMPT_PATTERNS: ReadonlyArray<readonly [PromptKind, RegExp]> = [
  ["confirm", py(String.raw`correct|\bright\b|resum|summary|todo bien|all good|looks good|bien asi`)],
  [
    "experience_platforms",
    py(String.raw`plataforma|platform|\bapps?\b|aplicacion|aplicativ|glovo|rappi|uber|didi|just eat`),
  ],
  ["availability", py(String.raw`disponibilidad|availab|tiempo completo|full[^\n]?time|jornada`)],
  ["preferred_schedule", py(String.raw`horario|turno|schedule|shift`)],
  [
    "driver_license",
    py(String.raw`licencia|licen[cs]e|carnet|carne\b|permiso de (conducir|manejo)|conducir|manejo`),
  ],
  ["experience_years", py(String.raw`experiencia|experience|cuanto tiempo|how long|how many years|cuantos anos`)],
  ["start_date", py(String.raw`empezar|empiezas|comenzar|incorporar|\bstart\b|cuando|\bwhen\b|fecha`)],
  [
    "zone",
    py(
      String.raw`\bzona|ciudad|barrio|colonia|municipio|alcaldia|delegacion|vives|\bvive\b|\bcity\b|\blive\b|neighbo|district|donde`,
    ),
  ],
  ["full_name", py(String.raw`nombre|apellido|llamas|\bname\b|call you`)],
];

// A question about how long the Candidate has done the work may name the apps in passing ("how many
// years on delivery apps?") and is still the years question; one that asks *which* apps is not.
const DURATION_RE = py(String.raw`cuantos anos|cuanto tiempo|anos de experiencia|how many years|how long|years of`);
const WHICH_PLATFORM_RE = py(
  String.raw`\b(que|cual|cuales|which|what)\s+(?:\w+\s+){0,2}(apps?|aplicaci\w+|aplicativ\w+|plataforma\w*|platform\w*)\b`,
);

// A field asked for as a statement or an imperative ("Necesito tu nombre y apellido.", "Tell me
// your city."). Real models sometimes re-ask with no question mark. The sentence must carry both a
// request cue addressed to the Candidate and the field's own topic words, so a Knockout that
// merely mentions the license still asks nothing. Accents are stripped.
const REQUEST_RE = py(
  [
    String.raw`\bnecesito (?:tu|tus|su|sus|el|la|los|las|que|saber|conocer)\b`,
    String.raw`\bme (?:falta|faltan)\b`,
    String.raw`\b(?:me gustaria|quisiera|quiero) (?:saber|conocer)\b`,
    String.raw`\b(?:dime|dimelo|digame|cuentame|indicame|dame|damelo|pasame|comparteme|enviame|mandame|escribeme|confirmame|aclarame)\b`,
    String.raw`\b(?:indica|comparte|envia|manda|escribe|confirma|aclara)\s+(?:tu|tus|su|sus|el|la)\b`,
    String.raw`\bpor favor,? (?:indica|dime|cuenta|comparte|envia|manda|escribe|confirma|aclara)`,
    String.raw`\bi need (?:your|to know)\b`,
    String.raw`\b(?:tell|give|send|show) me\b|\blet me know\b`,
    String.raw`\bplease,? (?:tell|send|share|give|provide|type|write|confirm|enter|let me know)\b`,
    String.raw`\b(?:send|share|give|provide|type|enter) (?:me )?(?:your|the)\b`,
    String.raw`\bi(?:'d| would) like to (?:know|get)\b`,
  ].join("|"),
);

// Abbreviations and ellipses whose periods are not sentence ends: "(Glovo, Uber Eats, etc.)?".
const NOT_A_STOP_RE = py(String.raw`\b(?:etc|e\.g|i\.e|p\.\s?ej|ej|vs|aprox|approx|tel)\.|\.{2,}`, "gi");
const DOT = "․"; // one dot leader, stands in for a period that does not end a sentence

// Emoji and pictographs. Phase 1 section 5: none in Knockout or Rejection messages.
// The variation selector U+FE0F sits outside the class, which the no-misleading-character-class
// lint rule would otherwise reject.
export const EMOJI_RE = /[\u{1f300}-\u{1faff}\u{2600}-\u{27bf}\u{1f000}-\u{1f2ff}]|\u{fe0f}/u;

const STRIP_ENDS_RE = new RegExp(`^${SPACE}+|${SPACE}+$`, "gu");

// Python's str.strip() with no argument.
const pyStrip = (text: string): string => text.replace(STRIP_ENDS_RE, "");

function stripAccents(text: string): string {
  return text.normalize("NFD").replace(/\p{Mn}/gu, "");
}

/** Lower-case, accent-free text for keyword matching. */
export function normalize(text: string): string {
  return stripAccents(text).toLowerCase();
}

// Python's truthiness of `interactive`: None and {} are both "no options block".
const present = (interactive: Interactive | null | undefined): interactive is Interactive =>
  interactive !== null && interactive !== undefined && Object.keys(interactive).length > 0;

const shield = (text: string): string => text.replace(NOT_A_STOP_RE, (m) => m.replaceAll(".", DOT));

/** The last sentence of `text` that ends in a question mark, or null. */
export function lastQuestion(text: string): string | null {
  const found = (shield(text).match(/[^.!?¿\n]*\?/gu) ?? [])
    .filter((q) => q.replace(/^[? \t]+|[? \t]+$/g, "") !== "")
    .map((q) => pyStrip(q).replaceAll(DOT, "."));
  return found.length > 0 ? found[found.length - 1] : null;
}

/** The last sentence that asks the Candidate for something without a question mark, or null.
 *  Only sentences with a request cue count (`REQUEST_RE`); a plain statement does not. */
export function lastRequest(text: string): string | null {
  const sentences = shield(text)
    .split(/[.!?¿¡\n]+/u)
    .map((part) => pyStrip(part.replaceAll(DOT, ".")));
  const found = sentences.filter((x) => x !== "" && REQUEST_RE.test(normalize(x)));
  return found.length > 0 ? found[found.length - 1] : null;
}

export function asksSomething(text: string, interactive?: Interactive | null): boolean {
  return present(interactive) || text.includes("?") || text.includes("¿");
}

/** The options of an interactive message when every one is a Zone id, else []. */
export function zoneOptions(interactive?: Interactive | null): NonNullable<Interactive["options"]> {
  const options = (present(interactive) ? interactive.options : undefined) ?? [];
  if (options.length > 0 && options.every((o) => String(o.id ?? "").startsWith("zone_"))) return [...options];
  return [];
}

function fromOptions(interactive: Interactive | null | undefined): PromptKind | null {
  if (!present(interactive)) return null;
  if (zoneOptions(interactive).length > 0) return "zone";
  const ids = (interactive.options ?? []).map((o) => String(o.id));
  const within = (allowed: Set<string>) => ids.length > 0 && ids.every((i) => allowed.has(i));
  if (within(AVAILABILITY_IDS)) return "availability";
  if (within(SCHEDULE_IDS)) return "preferred_schedule";
  if (within(PLATFORM_IDS)) return "experience_platforms";
  if (ids.length > 0 && ids.every((i) => DATE_ID.test(i))) return "start_date";
  return null;
}

function kindOf(sentence: string): PromptKind | null {
  const norm = normalize(sentence);
  for (const [kind, pattern] of PROMPT_PATTERNS) {
    if (!pattern.test(norm)) continue;
    if (kind === "experience_platforms" && DURATION_RE.test(norm) && !WHICH_PLATFORM_RE.test(norm)) {
      return "experience_years";
    }
    return kind;
  }
  return null;
}

/**
 * Which question does this agent message ask? null when it asks no recognisable one.
 *
 * Looks at the option ids first (they are canonical), then at the last question in the text, then
 * at the last sentence that asks in a statement or imperative ("Necesito tu nombre y apellido.").
 * A plain statement is never a prompt: a Knockout template mentions the license without asking
 * for it.
 */
export function detectPrompt(text: string, interactive?: Interactive | null): PromptKind | null {
  const byOptions = fromOptions(interactive);
  if (byOptions) return byOptions;
  const question = lastQuestion(text);
  let kind = question !== null ? kindOf(question) : null;
  if (kind === null) {
    // No question mark, or a question with no topic: the agent may have asked in a statement.
    const request = lastRequest(text);
    kind = request !== null ? kindOf(request) : null;
  }
  return kind;
}
