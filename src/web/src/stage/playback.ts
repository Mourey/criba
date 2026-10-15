// The Demo player's brain: a pure state machine that makes the choices of the Python persona driver
// (src/tests/sim/driver.py, class `_Session`). No I/O and no timers: `next(state, observation)`
// returns the new state and one action, and the hook (usePlayer.ts) carries the action out. The
// citations are driver.py's lines at the time of writing:
//   `_prompt_of` :545, `_next_reply` :553 (with `corrected`), `_zone_pick` :578,
//   `_with_zone_choice` :610, `_next_unasked` :625, `play` :630-722, `_pick_option` :429,
//   `say` :441 (the `option_expired` fallback :476), `fire` :509.
// Question detection is detect.ts, a port of detect.py. tests/unit/playback.test.ts replays every
// run recorded in tests/fixtures/detect-corpus.json and requires the same sends, taps and end.
//
// Where the driver blocks on the network, this machine returns `wait` and is called again with the
// next observation. The driver's `timeout` (no reply in the allowed time) is the hook's 60 s pause,
// not a stop, so it is not a state here. Its `no_reply` end cannot occur in this loop: a turn with
// no reply is a timeout, never an empty reply.

import type { ChatMessage } from "../api/types.ts";
import { asksSomething, detectPrompt, normalize, zoneOptions, type Interactive, type PromptKind } from "./detect.ts";
import personasFile from "./personas.json";

// ---- the persona file --------------------------------------------------------------------------

export interface PersonaReply {
  text: string;
  lang: "es" | "en" | null;
  pick_option: number | string | null;
  covers: string[];
}

/** A confirm reply. `corrects` and `hint` are the persona's `field` and `query`: the driver remembers
 *  a correction by its field and picks a corrected Zone by the words of its hint. */
export interface ConfirmReply extends PersonaReply {
  corrects: string | null;
  hint: string | null;
}

export interface PersonaInterrupt {
  at: string;
  advance: string[];
  say: PersonaReply | null;
  then: "continue" | "stop";
}

export interface PersonaEntry {
  id: string;
  identity: { name: string; phone: string; country: "ES" | "MX"; locale: "es-ES" | "es-MX" | "en"; entry: string };
  opening: PersonaReply;
  answers: Record<string, PersonaReply[]>;
  zone_choice: number | string | null;
  confirm: ConfirmReply[];
  interrupts: PersonaInterrupt[];
  expect: { stage: string; status: string; outcome: string | null; events: string[] };
  playable: boolean;
  unplayable_reason: string | null;
  persona_digest: string;
}

interface PersonasFile {
  fieldOrder: string[];
  personas: PersonaEntry[];
}

const FILE = personasFile as unknown as PersonasFile;

/** The fields in the order the agent collects them (`sim.personas.schema.FIELD_ORDER`). */
export const FIELD_ORDER: readonly string[] = FILE.fieldOrder;
export const PERSONAS: readonly PersonaEntry[] = FILE.personas;

// ---- limits the driver has -----------------------------------------------------------------------

/** driver.py `max_turns`. */
export const MAX_TURNS = 60;
/** driver.py `max_reprompts`: consecutive unplaced re-asks answered before the run stops. */
export const MAX_REPROMPTS = 4;
/** driver.py `KNOCKOUT_FIELDS`. */
const KNOCKOUT_FIELDS: readonly string[] = ["zone", "driver_license"];
const TERMINAL_STAGES: readonly string[] = ["qualified", "approved", "interview_booked"];

// ---- pace ----------------------------------------------------------------------------------------

export interface Pace {
  /** Typing speed. 0 types the whole text at once. */
  charsPerSecond: number;
  /** The pause before each message, in seconds. */
  pauseSeconds: number;
}

export const DEFAULT_PACE: Pace = { charsPerSecond: 30, pauseSeconds: 1.2 };

const defaultYes = (persona: PersonaEntry): string => (persona.identity.locale === "en" ? "Yes" : "Sí");

/**
 * The seconds the player adds to a run at `pace`: a pause before each message plus the typing time of
 * its characters. It counts every reply the persona can send once (the opening, each answer, the
 * confirm list or the plain yes, each interrupt). Zero settings give the fastest pace.
 */
export function scheduleSeconds(persona: PersonaEntry, pace: Pace = DEFAULT_PACE): number {
  const replies: string[] = [persona.opening.text];
  for (const list of Object.values(persona.answers)) replies.push(...list.map((r) => r.text));
  replies.push(...(persona.confirm.length > 0 ? persona.confirm.map((r) => r.text) : [defaultYes(persona)]));
  for (const interrupt of persona.interrupts) if (interrupt.say) replies.push(interrupt.say.text);
  const chars = replies.reduce((sum, text) => sum + [...text].length, 0);
  const typing = pace.charsPerSecond > 0 ? chars / pace.charsPerSecond : 0;
  return replies.length * Math.max(0, pace.pauseSeconds) + typing;
}

// ---- what the player sees and does ---------------------------------------------------------------

/** The Application's Stage, status and Outcome, from the stage session's lookup. */
export interface ApplicationState {
  stage?: string | null;
  status?: string | null;
  outcome?: string | null;
}

export interface Observation {
  /** The conversation so far, oldest first (the hook's `Conversation.messages`). */
  messages: readonly ChatMessage[];
  /** The agent run has finished: `runFinished`, or typing off for a second when it is null. */
  settled: boolean;
  application: ApplicationState | null;
  /** The tap the last action asked for was refused (it was not the answerable message). */
  tapRejected: boolean;
}

export type StopReason = "completed" | "knockout" | "stopped" | "max_turns" | "terminal" | `no_scripted_reply:${string}`;

export interface Stop {
  reason: StopReason;
  /** For `terminal`: what ended it ("stage qualified"). */
  detail: string | null;
}

/** Why a message was chosen, for the control's last-decision line. */
export interface Decision {
  purpose: string;
  source: "opening" | "interrupt" | "answer" | "confirm" | "confirm_default" | "correction" | "tap_rejected";
  /** What `detectPrompt` found in the agent's message (answers only). */
  detected: PromptKind | null;
  /** The agent's message was not recognised and the player guessed the field. */
  guessed: boolean;
  /** 1-based position of the reply among the persona's replies for the question, and how many. */
  index: number;
  count: number;
  /** The persona ran out of replies and repeats its last one. */
  repeated: boolean;
  /** For an interrupt, the moment it fired at. */
  at: string | null;
}

export interface SendAction {
  type: "send";
  text: string;
  purpose: string;
  decision: Decision;
}

export interface TapAction {
  type: "tap";
  /** The agent message that carries the options. */
  messageId: number;
  optionId: string;
  label: string;
  /** What to send as text when the tap is refused, as the driver does on `option_expired`. */
  fallbackText: string;
  purpose: string;
  decision: Decision;
}

export type Action = SendAction | TapAction | { type: "wait" } | { type: "stop"; stop: Stop };

// ---- state ---------------------------------------------------------------------------------------

/** Where the interrupts of one moment have got to, and what follows them. */
interface FireContext {
  at: string;
  promptMsg: ChatMessage | null;
  cursor: number;
  after: AfterFire;
}

type AfterFire =
  | { tag: "opening" }
  | { tag: "knockout" }
  | { tag: "answer"; kind: string; promptMsg: ChatMessage | null; guessed: boolean; afterBackout: boolean };

type Resume =
  | { tag: "opening" }
  | { tag: "answer" }
  | { tag: "interrupt"; ctx: FireContext; then: "continue" | "stop" };

interface Waiting {
  tag: "waiting";
  /** The newest Candidate message id when the action went out: ours is the first above it. */
  baseInboundId: number;
  resume: Resume;
  tapped: { text: string; purpose: string; decision: Decision } | null;
}

type Step = { tag: "begin" } | Waiting | { tag: "done" };

export interface PlayerState {
  readonly persona: PersonaEntry;
  readonly fieldOrder: readonly string[];
  /** driver `asked`: how many times each field's reply has been taken. */
  readonly asked: Readonly<Record<string, number>>;
  readonly confirmUsed: number;
  /** driver `corrected`: a correction at the confirm step, per field. Kept by reference. */
  readonly corrected: Readonly<Record<string, PersonaReply>>;
  /** Indexes of the interrupts that have fired. */
  readonly fired: readonly number[];
  /** Loop iterations begun (driver `for _ in range(max_turns)`). */
  readonly turns: number;
  readonly reprompts: number;
  readonly pending: string | null;
  readonly lastSentField: string | null;
  readonly step: Step;
  readonly decision: Decision | null;
  readonly stop: Stop | null;
}

type Draft = { -readonly [K in keyof PlayerState]: PlayerState[K] };

export function initialState(persona: PersonaEntry, fieldOrder: readonly string[] = FIELD_ORDER): PlayerState {
  return {
    persona,
    fieldOrder,
    asked: {},
    confirmUsed: 0,
    corrected: {},
    fired: [],
    turns: 0,
    reprompts: 0,
    pending: null,
    lastSentField: null,
    step: { tag: "begin" },
    decision: null,
    stop: null,
  };
}

// ---- the driver's helpers ------------------------------------------------------------------------

const hasPending = (s: Draft, at: string): boolean => s.persona.interrupts.some((i, idx) => i.at === at && !s.fired.includes(idx));

const defaultLang = (persona: PersonaEntry): "es" | "en" => (persona.identity.locale === "en" ? "en" : "es");

/** driver.py `_pick_option`. */
function pickOption(reply: PersonaReply, interactive: Interactive | null | undefined): { id: string; label: string } | null {
  if (reply.pick_option === null || !interactive) return null;
  const options = interactive.options ?? [];
  const choice = reply.pick_option;
  const found =
    typeof choice === "number"
      ? choice >= 0 && choice < options.length
        ? options[choice]
        : undefined
      : options.find((o) => normalize(String(o.label ?? "")).includes(normalize(String(choice))));
  return found ? { id: String(found.id), label: String(found.label ?? "") } : null;
}

/** driver.py `_prompt_of`: the question asked by the latest agent message that asks a recognisable one. */
function promptOf(agent: readonly ChatMessage[]): { kind: PromptKind | null; message: ChatMessage | null } {
  for (let i = agent.length - 1; i >= 0; i--) {
    const kind = detectPrompt(agent[i].text, agent[i].interactive);
    if (kind) return { kind, message: agent[i] };
  }
  return { kind: null, message: null };
}

/** driver.py `_next_unasked`: the first field in collection order the persona has neither sent nor covered. */
function nextUnasked(s: Draft): string | null {
  const covered = new Set(s.persona.opening.covers);
  return s.fieldOrder.find((f) => !covered.has(f) && !s.asked[f]) ?? null;
}

const words = (text: string): string[] => normalize(text).match(/[a-z0-9]+/g) ?? [];

/** driver.py `_zone_pick`: the index of the Zone option the persona taps. */
function zonePick(s: Draft, options: ReadonlyArray<{ label?: string }>): number {
  const hint = s.persona.zone_choice;
  if (typeof hint === "number" && hint >= 0 && hint < options.length) return hint;
  if (typeof hint === "string") {
    const needle = normalize(hint).trim();
    const index = options.findIndex((o) => normalize(String(o.label ?? "")).includes(needle));
    if (index >= 0) return index;
  }
  const correction = s.corrected.zone as ConfirmReply | undefined;
  let said: string[];
  if (correction) {
    said = [correction.text, correction.hint ?? ""];
  } else {
    said = (s.persona.answers.zone ?? []).map((r) => r.text);
    if (s.persona.opening.covers.includes("zone")) said.push(s.persona.opening.text);
  }
  const wanted = new Set(words(said.join(" ")).filter((w) => w.length > 2));
  let best = 0;
  let bestScore = 0;
  options.forEach((option, index) => {
    const label = new Set(words(String(option.label ?? "")));
    let score = 0;
    for (const w of wanted) if (label.has(w)) score += 1;
    if (score > bestScore) {
      best = index;
      bestScore = score;
    }
  });
  return best;
}

/** driver.py `_with_zone_choice`: tap a Zone option when the agent offers Zone ids and the reply names none. */
function withZoneChoice(s: Draft, reply: PersonaReply, promptMsg: ChatMessage): PersonaReply {
  const options = zoneOptions(promptMsg.interactive);
  if (options.length === 0 && reply === s.corrected.zone && typeof reply.pick_option === "string") {
    // A corrected Zone asked again in text: the Candidate types the Zone they would tap.
    return { text: reply.pick_option, lang: reply.lang, pick_option: null, covers: [] };
  }
  if (options.length === 0 || reply.pick_option !== null) return reply;
  return { ...reply, pick_option: zonePick(s, options) };
}

interface Taken {
  reply: PersonaReply;
  source: Decision["source"];
  index: number;
  count: number;
  repeated: boolean;
}

/** driver.py `_next_reply`: the reply for a question, counting it. Null when the persona has none. */
function nextReply(s: Draft, kind: string): Taken | null {
  const persona = s.persona;
  if (kind === "confirm") {
    const index = s.confirmUsed;
    s.confirmUsed += 1;
    if (index < persona.confirm.length) {
      const correction = persona.confirm[index];
      if (correction.corrects !== null) s.corrected = { ...s.corrected, [correction.corrects]: correction };
      return { reply: correction, source: "confirm", index: index + 1, count: persona.confirm.length, repeated: false };
    }
    const lang = defaultLang(persona);
    return {
      reply: { text: lang === "en" ? "Yes" : "Sí", lang, pick_option: null, covers: [] },
      source: "confirm_default",
      index: 1,
      count: 1,
      repeated: false,
    };
  }
  const corrected = s.corrected[kind];
  if (corrected) {
    // The agent asks again for a field the Candidate just corrected: they repeat the correction.
    return { reply: corrected, source: "correction", index: 1, count: 1, repeated: true };
  }
  const replies = persona.answers[kind];
  if (!replies || replies.length === 0) return null;
  const n = s.asked[kind] ?? 0;
  s.asked = { ...s.asked, [kind]: n + 1 };
  return { reply: replies[Math.min(n, replies.length - 1)], source: "answer", index: Math.min(n, replies.length - 1) + 1, count: replies.length, repeated: n >= replies.length };
}

// ---- actions -------------------------------------------------------------------------------------

const newestInboundId = (messages: readonly ChatMessage[]): number => messages.reduce((id, m) => (m.direction === "inbound" && m.id > id ? m.id : id), 0);

const finish = (s: Draft, reason: StopReason, detail: string | null = null): Action => {
  s.step = { tag: "done" };
  s.stop = { reason, detail };
  return { type: "stop", stop: s.stop };
};

/** driver.py `say`: send one reply as text, or tap the option it picks on the prompting message. */
function say(s: Draft, reply: PersonaReply, purpose: string, promptMsg: ChatMessage | null, decision: Decision, resume: Resume, base: number): Action {
  const option = promptMsg ? pickOption(reply, promptMsg.interactive) : null;
  s.decision = decision;
  if (option && promptMsg) {
    s.step = { tag: "waiting", baseInboundId: base, resume, tapped: { text: reply.text, purpose, decision } };
    return { type: "tap", messageId: promptMsg.id, optionId: option.id, label: option.label, fallbackText: reply.text, purpose, decision };
  }
  s.step = { tag: "waiting", baseInboundId: base, resume, tapped: null };
  return { type: "send", text: reply.text, purpose, decision };
}

const interruptDecision = (at: string): Decision => ({ purpose: `interrupt:${at}`, source: "interrupt", detected: null, guessed: false, index: 1, count: 1, repeated: false, at });

/** driver.py `fire`: run the persona's interrupts for `ctx.at`, one say at a time. */
function runFire(s: Draft, ctx: FireContext, last: readonly ChatMessage[], base: number): Action {
  const interrupts = s.persona.interrupts;
  for (let idx = ctx.cursor; idx < interrupts.length; idx++) {
    const interrupt = interrupts[idx];
    if (interrupt.at !== ctx.at || s.fired.includes(idx)) continue;
    s.fired = [...s.fired, idx];
    if (interrupt.say) {
      const resume: Resume = { tag: "interrupt", ctx: { ...ctx, cursor: idx + 1 }, then: interrupt.then };
      return say(s, interrupt.say, `interrupt:${ctx.at}`, ctx.promptMsg, interruptDecision(ctx.at), resume, base);
    }
    if (interrupt.then === "stop") return endFire(s, ctx, true, last, base);
  }
  return endFire(s, ctx, false, last, base);
}

/** What follows a `fire`: `last` is the agent's reply to its last say (driver `after`). */
function endFire(s: Draft, ctx: FireContext, stop: boolean, last: readonly ChatMessage[], base: number): Action {
  const after = ctx.after;
  if (after.tag === "knockout") return finish(s, "knockout");
  if (stop) return finish(s, "stopped");
  if (after.tag === "opening") return sendOpening(s, base);
  if (!after.afterBackout && last.length > 0 && hasPending(s, "after_backout_prompt")) {
    const backout: FireContext = {
      at: "after_backout_prompt",
      promptMsg: last[last.length - 1],
      cursor: 0,
      after: { ...after, afterBackout: true },
    };
    return runFire(s, backout, [], base);
  }
  return sendAnswer(s, after, base);
}

function sendOpening(s: Draft, base: number): Action {
  const decision: Decision = { purpose: "opening", source: "opening", detected: null, guessed: false, index: 1, count: 1, repeated: false, at: null };
  return say(s, s.persona.opening, "opening", null, decision, { tag: "opening" }, base);
}

function sendAnswer(s: Draft, after: Extract<AfterFire, { tag: "answer" }>, base: number): Action {
  const taken = nextReply(s, after.kind);
  if (!taken) return finish(s, `no_scripted_reply:${after.kind}`);
  let reply = taken.reply;
  if (after.kind === "zone" && after.promptMsg) reply = withZoneChoice(s, reply, after.promptMsg);
  s.pending = after.kind;
  s.lastSentField = after.kind === "confirm" ? null : after.kind;
  const decision: Decision = {
    purpose: `answer:${after.kind}`,
    source: taken.source,
    detected: after.kind as PromptKind,
    guessed: after.guessed,
    index: taken.index,
    count: taken.count,
    repeated: taken.repeated,
    at: null,
  };
  return say(s, reply, `answer:${after.kind}`, after.promptMsg, decision, { tag: "answer" }, base);
}

/** PB5's terminal rule: an Outcome is set, the status is closed, or the Stage is a finished one. */
function terminal(s: Draft, application: ApplicationState | null): string | null {
  if (!application || hasPending(s, "after_knockout")) return null;
  if (application.outcome) return `outcome ${application.outcome}`;
  if (application.status === "closed") return "status closed";
  if (application.stage && TERMINAL_STAGES.includes(application.stage)) return `stage ${application.stage}`;
  return null;
}

/** The top of driver.py's `for _ in range(max_turns)` loop, with `agent` the replies to the last send. */
function loop(s: Draft, agent: readonly ChatMessage[], application: ApplicationState | null, base: number): Action {
  if (s.turns >= MAX_TURNS) return finish(s, "max_turns");
  s.turns += 1;
  const found = promptOf(agent);
  let kind = found.kind;
  let promptMsg = found.message;

  // A Knockout answers a Knock-out check (Zone, license) with a message that asks nothing. Anything
  // that asks, even about the same field, is a clarification or a re-ask, not a Knockout.
  const knockedOut =
    (s.persona.expect.outcome === "knockout" || hasPending(s, "after_knockout")) &&
    s.lastSentField !== null &&
    KNOCKOUT_FIELDS.includes(s.lastSentField) &&
    kind === null &&
    !agent.some((m) => asksSomething(m.text, m.interactive));
  if (knockedOut) {
    if (hasPending(s, "after_knockout")) return runFire(s, { at: "after_knockout", promptMsg: null, cursor: 0, after: { tag: "knockout" } }, [], base);
    return finish(s, "knockout");
  }

  let guessed = false;
  if (kind === null) {
    const guess = s.pending ?? nextUnasked(s);
    const lastMessage = agent[agent.length - 1];
    if (asksSomething(lastMessage.text, lastMessage.interactive) && s.reprompts < MAX_REPROMPTS && guess !== null) {
      kind = guess as PromptKind;
      promptMsg = lastMessage;
      guessed = true;
      s.reprompts += 1;
    } else {
      return finish(s, "completed");
    }
  } else {
    s.reprompts = 0;
  }

  const done = terminal(s, application);
  if (done) return finish(s, "terminal", done);

  const ctx: FireContext = { at: kind, promptMsg, cursor: 0, after: { tag: "answer", kind, promptMsg, guessed, afterBackout: false } };
  return runFire(s, ctx, [], base);
}

// ---- the step function ---------------------------------------------------------------------------

/**
 * One step of the player. Pure: it returns a new state and does nothing else. Call it with the
 * current observation until it returns `stop`; `send` and `tap` are for the hook to carry out, and
 * `wait` means call again when the observation changes.
 */
export function next(state: PlayerState, observation: Observation): { state: PlayerState; action: Action } {
  const s: Draft = { ...state, asked: { ...state.asked }, corrected: { ...state.corrected }, fired: [...state.fired] };
  const messages = observation.messages;
  const base = newestInboundId(messages);
  const step = s.step;

  if (step.tag === "done") return { state: s, action: { type: "stop", stop: s.stop! } };

  if (step.tag === "begin") {
    const start: FireContext = { at: "start", promptMsg: null, cursor: 0, after: { tag: "opening" } };
    return { state: s, action: runFire(s, start, [], base) };
  }

  // A tap the page refused before it landed: send the reply's text, as the driver does on `option_expired`.
  if (observation.tapRejected && step.tapped && base === step.baseInboundId) {
    const { text, purpose, decision } = step.tapped;
    const fallback: Decision = { ...decision, source: "tap_rejected" };
    s.decision = fallback;
    s.step = { ...step, tapped: null };
    return { state: s, action: { type: "send", text, purpose, decision: fallback } };
  }

  // Our message has to be on screen before anything above it is a reply to it.
  if (base <= step.baseInboundId) return { state: s, action: { type: "wait" } };
  const replies = messages.filter((m) => m.direction === "outbound" && m.id > base);
  if (!observation.settled || replies.length === 0) return { state: s, action: { type: "wait" } };

  const resume = step.resume;
  if (resume.tag === "interrupt") {
    const action = resume.then === "stop" ? endFire(s, resume.ctx, true, replies, base) : runFire(s, resume.ctx, replies, base);
    return { state: s, action };
  }
  return { state: s, action: loop(s, replies, observation.application, base) };
}
