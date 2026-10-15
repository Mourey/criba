import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createElement, useState, useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import corpus from "../fixtures/detect-corpus.json";
import type { ApplicationSummary, ChatMessage, ChatStartRequest } from "../../src/api/types.ts";
import type { Conversation } from "../../src/api/useConversation.ts";
import {
  DEFAULT_PACE,
  FIELD_ORDER,
  initialState,
  MAX_REPROMPTS,
  MAX_TURNS,
  next,
  PERSONAS,
  scheduleSeconds,
  type Action,
  type ApplicationState,
  type ConfirmReply,
  type Observation,
  type PersonaEntry,
  type PersonaReply,
  type PlayerState,
  type SendAction,
  type TapAction,
} from "../../src/stage/playback.ts";
import { PlayerControls } from "../../src/stage/PlayerControls.tsx";
import { describeDecision } from "../../src/stage/playerCopy.ts";
import { StagePlayerContext, type StagePlayerValue } from "../../src/stage/useStageSession.ts";
import { detail, summary } from "./fixtures.ts";
import { mockApi } from "./helpers.tsx";

// The state machine in stage/playback.ts makes the Python driver's choices (src/tests/sim/driver.py).
// Block 1 replays every run recorded in the detect corpus (`--record` against the stub stack) and
// requires the same sends, the same taps and the same end. Block 2 pins each rule on small personas.
// The hook, the control and the timers are step 6's, with their own blocks in this file.

// ---- helpers -------------------------------------------------------------------------------

type Options = ReadonlyArray<readonly [string, string]>;
type Say = string | { text: string; options: Options };

function chat(o: { id: number; direction: "inbound" | "outbound"; text: string; options?: Options; replyTo?: { message_id: number; option_id: string } }): ChatMessage {
  return {
    id: o.id,
    message_id: null,
    direction: o.direction,
    sender: o.direction === "inbound" ? "candidate" : "agent",
    sender_label: null,
    text: o.text,
    interactive: o.options ? { style: "buttons", options: o.options.map(([id, label]) => ({ id, label })) } : null,
    reply_to: o.replyTo ?? null,
    links: [],
    attachments: [],
    created_at: "2026-10-10T10:00:00Z",
  };
}

const observation = (messages: ChatMessage[], extra: Partial<Observation> = {}): Observation => ({
  messages,
  settled: true,
  application: null,
  tapRejected: false,
  ...extra,
});

const reply = (text: string, extra: Partial<PersonaReply> = {}): PersonaReply => ({ text, lang: null, pick_option: null, covers: [], ...extra });
const plain = (text: string): ConfirmReply => ({ ...reply(text), corrects: null, hint: null });
const correction = (text: string, corrects: string, hint: string | null, extra: Partial<PersonaReply> = {}): ConfirmReply => ({
  ...reply(text, extra),
  corrects,
  hint,
});

function persona(over: Partial<PersonaEntry> = {}): PersonaEntry {
  return {
    id: "test_mx",
    identity: { name: "Test", phone: "+525500000999", country: "MX", locale: "es-MX", entry: "candidate_writes_first" },
    opening: reply("Hola"),
    answers: {},
    zone_choice: null,
    confirm: [],
    interrupts: [],
    expect: { stage: "greeting", status: "open", outcome: null, events: [] },
    playable: true,
    unplayable_reason: null,
    persona_digest: "0".repeat(64),
    ...over,
  };
}

const ZONES: Say = {
  text: "Encontré varias zonas con ese nombre. ¿Cuál es la tuya?",
  options: [
    ["zone_ang", "Angelópolis, Puebla"],
    ["zone_centro", "Centro, Puebla"],
    ["zone_paz", "La Paz, Puebla"],
  ],
};

type Run = { actions: Array<SendAction | TapAction>; state: PlayerState; stop: Extract<Action, { type: "stop" }>["stop"] | null; exhausted: boolean; messages: ChatMessage[] };

/** Plays `script` (one batch of agent messages per Candidate message) through `next`. */
function drive(p: PersonaEntry, script: Say[][], application: ApplicationState | null = null): Run {
  let state = initialState(p);
  const messages: ChatMessage[] = [];
  const actions: Array<SendAction | TapAction> = [];
  let id = 1;
  let batch = 0;
  for (let guard = 0; guard < 400; guard++) {
    const result = next(state, observation(messages, { application }));
    state = result.state;
    const action = result.action;
    if (action.type === "stop") return { actions, state, stop: action.stop, exhausted: false, messages };
    if (action.type === "wait") throw new Error(`waited with everything settled after ${actions.length} actions`);
    actions.push(action);
    messages.push(
      action.type === "tap"
        ? chat({ id: id++, direction: "inbound", text: action.label, replyTo: { message_id: action.messageId, option_id: action.optionId } })
        : chat({ id: id++, direction: "inbound", text: action.text }),
    );
    if (batch >= script.length) return { actions, state, stop: null, exhausted: true, messages };
    for (const say of script[batch++]) {
      messages.push(typeof say === "string" ? chat({ id: id++, direction: "outbound", text: say }) : chat({ id: id++, direction: "outbound", text: say.text, options: say.options }));
    }
  }
  throw new Error("no end after 400 actions");
}

const sends = (run: Run): string[] => run.actions.flatMap((a) => (a.type === "send" ? [a.text] : []));

// ---- block 1: every recorded run replays exactly -----------------------------------------------

type RecordedRun = (typeof corpus.runs)[number];

function replay(run: RecordedRun): string[] {
  const diffs: string[] = [];
  const found = PERSONAS.find((p) => p.id === run.persona_id);
  if (!found) return [`${run.persona_id}: no such persona in personas.json`];
  if (found.persona_digest !== run.persona_digest) diffs.push(`${run.persona_id}: the run was recorded for another version of the persona`);
  let state = initialState(found);
  const messages: ChatMessage[] = [];
  const idOf = new Map<string, number>();
  let id = 1;
  let turn = 0;
  for (let guard = 0; guard < 200; guard++) {
    const result = next(state, observation(messages, { application: null }));
    state = result.state;
    const action = result.action;
    if (action.type === "wait") return [...diffs, `${run.persona_id} turn ${turn}: waited although the run had settled`];
    if (action.type === "stop") {
      if (turn < run.turns.length) diffs.push(`${run.persona_id} turn ${turn}: stopped (${action.stop.reason}) before the recorded turn ${turn}`);
      else if (action.stop.reason !== run.ended) diffs.push(`${run.persona_id}: ended ${action.stop.reason}, recorded ${run.ended}`);
      return diffs;
    }
    const recorded = run.turns[turn];
    if (!recorded) return [...diffs, `${run.persona_id} turn ${turn}: sent ${JSON.stringify(action)} after the recorded run ended (${run.ended})`];
    const sent = recorded.sent;
    if (sent.option_id === null) {
      if (action.type !== "send" || action.text !== sent.text) diffs.push(`${run.persona_id} turn ${turn}: recorded send ${JSON.stringify(sent.text)}, got ${JSON.stringify(action)}`);
    } else if (action.type !== "tap" || action.optionId !== sent.option_id) {
      diffs.push(`${run.persona_id} turn ${turn}: recorded tap ${sent.option_id}, got ${JSON.stringify(action)}`);
    } else if (action.messageId !== idOf.get(`${sent.prompt_turn}:${sent.prompt_index}`)) {
      diffs.push(`${run.persona_id} turn ${turn}: tapped message ${action.messageId}, recorded prompt turn ${sent.prompt_turn} reply ${sent.prompt_index}`);
    }
    if (action.purpose !== sent.purpose) diffs.push(`${run.persona_id} turn ${turn}: purpose ${action.purpose}, recorded ${sent.purpose}`);
    messages.push(
      action.type === "tap"
        ? chat({ id: id++, direction: "inbound", text: action.label, replyTo: { message_id: action.messageId, option_id: action.optionId } })
        : chat({ id: id++, direction: "inbound", text: action.text }),
    );
    recorded.replies.forEach((r, index) => {
      idOf.set(`${turn}:${index}`, id);
      messages.push(chat({ id: id++, direction: "outbound", text: r.text, options: r.interactive?.options.map((o) => [o.id, o.label] as const) }));
    });
    turn += 1;
  }
  return [...diffs, `${run.persona_id}: no end after 200 turns`];
}

describe("replaying the recorded runs", () => {
  it("has a run for every playable persona the corpus was recorded with", () => {
    expect(corpus.runs.length).toBeGreaterThanOrEqual(10);
    for (const r of corpus.runs) {
      const found = PERSONAS.find((p) => p.id === r.persona_id);
      expect(found?.playable, r.persona_id).toBe(true);
    }
  });

  it.each(corpus.runs.map((r) => [r.persona_id, r] as const))("%s: the same sends, taps and end as the Python driver", (_id, run) => {
    expect(replay(run)).toEqual([]);
  });

  it("replays every run with 0 differences", () => {
    const diffs = corpus.runs.flatMap(replay);
    expect(diffs, `${corpus.runs.length} runs replayed`).toEqual([]);
  });
});

// ---- block 2: the driver's rules ---------------------------------------------------------------

describe("the opening and the start interrupt", () => {
  it("sends the opening first, with no tap and the purpose the driver records", () => {
    const run = drive(persona(), []);
    expect(run.actions[0]).toMatchObject({ type: "send", text: "Hola", purpose: "opening" });
    expect(run.exhausted).toBe(true);
  });

  it("fires a start interrupt before the opening and ignores what the agent answers it", () => {
    const p = persona({
      interrupts: [{ at: "start", advance: [], say: reply("buenas"), then: "continue" }],
      answers: { zone: [reply("Puebla")] },
    });
    const run = drive(p, [["Hola, ¿en qué te ayudo?"], ["¿En qué zona vives?"], ["Gracias."]]);
    expect(sends(run)).toEqual(["buenas", "Hola", "Puebla"]);
    expect(run.actions.map((a) => a.purpose)).toEqual(["interrupt:start", "opening", "answer:zone"]);
    expect(run.stop?.reason).toBe("completed");
  });

  it("stops after the reply to a start interrupt that says then stop", () => {
    const p = persona({ interrupts: [{ at: "start", advance: [], say: reply("buenas"), then: "stop" }] });
    const run = drive(p, [["Hola."]]);
    expect(sends(run)).toEqual(["buenas"]);
    expect(run.stop).toMatchObject({ reason: "stopped" });
  });
});

describe("waiting", () => {
  const p = persona({ answers: { zone: [reply("Puebla")] } });

  it("waits until its own message is visible, whatever older replies are on screen", () => {
    // An earlier exchange is on screen when the opening goes out.
    const old = [chat({ id: 1, direction: "inbound", text: "x" }), chat({ id: 2, direction: "outbound", text: "¿En qué zona vives?" })];
    const first = next(initialState(p), observation(old));
    expect(first.action).toMatchObject({ type: "send", text: "Hola" });
    // The opening has not landed: the old reply is not its answer.
    expect(next(first.state, observation(old)).action).toEqual({ type: "wait" });
  });

  it("waits while the run has not settled, and while it settled with nothing to read", () => {
    const first = next(initialState(p), observation([]));
    const landed = [chat({ id: 1, direction: "inbound", text: "Hola" })];
    expect(next(first.state, observation(landed)).action).toEqual({ type: "wait" });
    const answered = [...landed, chat({ id: 2, direction: "outbound", text: "¿En qué zona vives?" })];
    expect(next(first.state, observation(answered, { settled: false })).action).toEqual({ type: "wait" });
    expect(next(first.state, observation(answered)).action).toMatchObject({ type: "send", text: "Puebla" });
  });

  it("reads only the messages above the newest Candidate message", () => {
    const first = next(initialState(p), observation([]));
    const messages = [
      chat({ id: 1, direction: "outbound", text: "¿Cuál es tu nombre?" }),
      chat({ id: 2, direction: "inbound", text: "Hola" }),
      chat({ id: 3, direction: "outbound", text: "¿En qué zona vives?" }),
    ];
    expect(next(first.state, observation(messages)).action).toMatchObject({ type: "send", text: "Puebla" });
  });

  it("repeats its last action as the stop once it has stopped", () => {
    const run = drive(persona(), [["Gracias."]]);
    expect(run.stop?.reason).toBe("completed");
    const again = next(run.state, observation(run.messages));
    expect(again.action).toMatchObject({ type: "stop", stop: { reason: "completed" } });
  });
});

describe("answers", () => {
  it("sends the n-th reply for a field and repeats the last one", () => {
    const p = persona({ answers: { zone: [reply("Puebla"), reply("Centro, Puebla")] } });
    const ask = "¿En qué zona vives?";
    const run = drive(p, [[ask], [ask], [ask], ["Gracias."]]);
    expect(sends(run)).toEqual(["Hola", "Puebla", "Centro, Puebla", "Centro, Puebla"]);
    expect(run.actions.map((a) => (a.decision.source === "answer" ? [a.decision.index, a.decision.count, a.decision.repeated] : null))).toEqual([
      null,
      [1, 2, false],
      [2, 2, false],
      [2, 2, true],
    ]);
  });

  it("stops when the persona has no reply for the field the agent asks", () => {
    const run = drive(persona(), [["¿Cuál es tu disponibilidad?"]]);
    expect(run.stop).toMatchObject({ reason: "no_scripted_reply:availability" });
    expect(sends(run)).toEqual(["Hola"]);
  });

  it("is done when the agent asks nothing", () => {
    const run = drive(persona(), [["Listo, gracias. El equipo te escribirá."]]);
    expect(run.stop).toMatchObject({ reason: "completed" });
  });

  it("answers the latest agent message that asks a recognisable question", () => {
    const p = persona({ answers: { zone: [reply("Puebla")], full_name: [reply("Ana")] } });
    const run = drive(p, [["¿Cuál es tu nombre?", "Un momento.", "¿En qué zona vives?", "Gracias por esperar."]]);
    expect(sends(run)[1]).toBe("Puebla");
  });

  it("records what it detected for the last-decision line", () => {
    const p = persona({ answers: { zone: [reply("Puebla")] } });
    const run = drive(p, [["¿En qué zona vives?"]]);
    const decision = run.actions[1].decision;
    expect(decision).toMatchObject({ detected: "zone", guessed: false, source: "answer", index: 1, count: 1 });
    expect(describeDecision(decision)).toBe("detected zone, sent reply 1 of 1");
    expect(describeDecision(run.actions[0].decision)).toBe("sent the opening");
  });
});

describe("a question that is not recognised", () => {
  const p = persona({ answers: { full_name: [reply("Ana"), reply("Ana Pérez")] } });
  const huh = "¿Perdón?";

  it("guesses the field pending, else the first one not yet asked, up to the driver's limit", () => {
    expect(MAX_REPROMPTS).toBe(4);
    const run = drive(p, [[huh], [huh], [huh], [huh], [huh]]);
    expect(sends(run)).toEqual(["Hola", "Ana", "Ana Pérez", "Ana Pérez", "Ana Pérez"]);
    expect(run.actions.slice(1).every((a) => a.decision.guessed)).toBe(true);
    expect(run.actions[1].decision.detected).toBe("full_name");
    expect(run.stop).toMatchObject({ reason: "completed" });
    expect(describeDecision(run.actions[1].decision)).toBe("guessed full_name, sent reply 1 of 2");
  });

  it("starts the count again once a question is recognised", () => {
    const q = persona({ answers: { full_name: [reply("Ana")], zone: [reply("Puebla")] } });
    const run = drive(q, [[huh], [huh], ["¿En qué zona vives?"], [huh], [huh], [huh], [huh], [huh]]);
    expect(sends(run)).toEqual(["Hola", "Ana", "Ana", "Puebla", "Puebla", "Puebla", "Puebla", "Puebla"]);
    expect(run.stop).toMatchObject({ reason: "completed" });
  });

  it("skips the fields the opening covers when it picks the first one not asked", () => {
    const q = persona({ opening: reply("Soy Ana", { covers: ["full_name"] }), answers: { full_name: [reply("Ana")], zone: [reply("Puebla")] } });
    const run = drive(q, [[huh]]);
    expect(sends(run)).toEqual(["Soy Ana", "Puebla"]);
  });

  it("is done when the unrecognised message asks nothing", () => {
    expect(drive(p, [["Anotado."]]).stop).toMatchObject({ reason: "completed" });
  });
});

describe("the confirm step", () => {
  const ask = "Resumen: Ana, Puebla. ¿Es correcto?";

  it("answers the summary with the confirm list, then a plain yes", () => {
    const p = persona({ confirm: [correction("no, el nombre es Ana María", "full_name", null), plain("sí, ahora sí")] });
    const run = drive(p, [[ask], [ask], [ask], ["Listo."]]);
    expect(sends(run)).toEqual(["Hola", "no, el nombre es Ana María", "sí, ahora sí", "Sí"]);
    expect(run.actions[3].decision).toMatchObject({ source: "confirm_default" });
  });

  it("says Yes in English and Sí otherwise", () => {
    const en = persona({ identity: { ...persona().identity, locale: "en" } });
    expect(sends(drive(en, [[ask], ["Done."]]))).toEqual(["Hola", "Yes"]);
    expect(sends(drive(persona(), [[ask], ["Listo."]]))).toEqual(["Hola", "Sí"]);
  });

  it("answers a corrected field with its correction when the agent asks it again", () => {
    const p = persona({
      confirm: [correction("no, me llamo Ana María", "full_name", null)],
      answers: { full_name: [reply("Ana")] },
    });
    const run = drive(p, [[ask], ["¿Cuál es tu nombre?"], ["¿Cuál es tu nombre?"], ["Gracias."]]);
    expect(sends(run)).toEqual(["Hola", "no, me llamo Ana María", "no, me llamo Ana María", "no, me llamo Ana María"]);
    expect(run.actions[2].decision.source).toBe("correction");
  });
});

describe("zone choice", () => {
  const withZone = (extra: Partial<PersonaEntry>) => persona({ answers: { zone: [reply("Puebla")] }, ...extra });

  it("taps the option at a zone_choice index", () => {
    const run = drive(withZone({ zone_choice: 2 }), [[ZONES]]);
    expect(run.actions[1]).toMatchObject({ type: "tap", optionId: "zone_paz", messageId: 2, label: "La Paz, Puebla", fallbackText: "Puebla" });
  });

  it("taps the option whose label holds a zone_choice substring, accents and case aside", () => {
    const run = drive(withZone({ zone_choice: "ANGELOPOLIS" }), [[ZONES]]);
    expect(run.actions[1]).toMatchObject({ type: "tap", optionId: "zone_ang" });
  });

  it("falls back to the words of what the persona said when zone_choice names nothing", () => {
    for (const zone_choice of [null, 7, "nowhere"]) {
      const run = drive(persona({ answers: { zone: [reply("Centro, Puebla")] }, zone_choice }), [[ZONES]]);
      expect(run.actions[1], String(zone_choice)).toMatchObject({ type: "tap", optionId: "zone_centro" });
    }
  });

  it("counts the opening's words when it covers the zone", () => {
    const p = persona({ opening: reply("Vivo en La Paz", { covers: ["zone"] }), answers: { zone: [reply("Puebla")] } });
    expect(drive(p, [[ZONES]]).actions[1]).toMatchObject({ optionId: "zone_paz" });
  });

  it("takes the first option when no word matches", () => {
    const run = drive(persona({ answers: { zone: [reply("xyz")] } }), [[ZONES]]);
    expect(run.actions[1]).toMatchObject({ optionId: "zone_ang" });
  });

  it("lets a reply's own pick_option win over zone_choice", () => {
    const p = persona({ zone_choice: 2, answers: { zone: [reply("Puebla", { pick_option: 0 })] } });
    expect(drive(p, [[ZONES]]).actions[1]).toMatchObject({ optionId: "zone_ang" });
    const byLabel = persona({ zone_choice: 2, answers: { zone: [reply("Puebla", { pick_option: "centro" })] } });
    expect(drive(byLabel, [[ZONES]]).actions[1]).toMatchObject({ optionId: "zone_centro" });
  });

  it("sends the text when the options hold no match for the reply's pick_option", () => {
    const p = persona({ answers: { zone: [reply("Puebla", { pick_option: 9 })] } });
    expect(drive(p, [[ZONES]]).actions[1]).toMatchObject({ type: "send", text: "Puebla" });
  });

  it("picks by the correction's text and hint after a Zone correction at the confirm step", () => {
    const p = persona({
      zone_choice: 0,
      answers: { zone: [reply("Puebla")] },
      confirm: [correction("no, mi zona es La Paz", "zone", "La Paz, Puebla")],
    });
    const run = drive(p, [["Resumen. ¿Es correcto?"], [ZONES]]);
    expect(run.actions[1]).toMatchObject({ type: "send", text: "no, mi zona es La Paz" });
    // zone_choice 0 would win outside a correction; inside one the words decide only when zone_choice names no option.
    expect(run.actions[2]).toMatchObject({ type: "tap", optionId: "zone_ang" });
    const free = drive(persona({ answers: { zone: [reply("Puebla")] }, confirm: [correction("no, mi zona es La Paz", "zone", "La Paz, Puebla")] }), [["Resumen. ¿Es correcto?"], [ZONES]]);
    expect(free.actions[2]).toMatchObject({ type: "tap", optionId: "zone_paz" });
    expect(free.actions[2].decision.source).toBe("correction");
  });

  it("types the corrected Zone's pick_option when the agent asks the Zone again in text", () => {
    const p = persona({
      answers: { zone: [reply("Puebla")] },
      confirm: [correction("no, mi zona es La Paz", "zone", "La Paz, Puebla", { pick_option: "La Paz" })],
    });
    const run = drive(p, [["Resumen. ¿Es correcto?"], ["¿En qué colonia?"], ["Gracias."]]);
    expect(sends(run)).toEqual(["Hola", "no, mi zona es La Paz", "La Paz"]);
  });
});

describe("a tap the page rejects", () => {
  const p = persona({ answers: { zone: [reply("Puebla"), reply("Centro, Puebla")], driver_license: [reply("sí")] } });

  function toTheTap() {
    const first = next(initialState(p), observation([]));
    const messages = [chat({ id: 1, direction: "inbound", text: "Hola" }), chat({ id: 2, direction: "outbound", text: "Qué zona", options: [["zone_ang", "Angelópolis, Puebla"], ["zone_centro", "Centro, Puebla"], ["zone_paz", "La Paz, Puebla"]] })];
    const tapped = next(first.state, observation(messages));
    return { messages, tapped };
  }

  it("sends the reply's text instead, as the driver does on option_expired", () => {
    const { messages, tapped } = toTheTap();
    // The words of every zone reply count ("Puebla" and "Centro, Puebla"), so Centro wins.
    expect(tapped.action).toMatchObject({ type: "tap", messageId: 2, optionId: "zone_centro", fallbackText: "Puebla" });
    // Nothing landed and the hook reports the rejected tap.
    const fallback = next(tapped.state, observation(messages, { tapRejected: true }));
    expect(fallback.action).toMatchObject({ type: "send", text: "Puebla", purpose: "answer:zone" });
    expect(describeDecision((fallback.action as Extract<Action, { type: "send" }>).decision)).toBe("tap not accepted, sent the reply text");
    // The text lands and the agent moves on: the reply counter moved once, not twice.
    const after = [...messages, chat({ id: 3, direction: "inbound", text: "Puebla" }), chat({ id: 4, direction: "outbound", text: "¿Tienes licencia?" })];
    const onward = next(fallback.state, observation(after));
    expect(onward.action).toMatchObject({ type: "send", text: "sí", purpose: "answer:driver_license" });
  });

  it("does not resend when no tap was rejected", () => {
    const { messages, tapped } = toTheTap();
    expect(next(tapped.state, observation(messages)).action).toEqual({ type: "wait" });
    const landed = [...messages, chat({ id: 3, direction: "inbound", text: "Centro, Puebla", replyTo: { message_id: 2, option_id: "zone_centro" } })];
    expect(next(tapped.state, observation(landed, { tapRejected: true })).action).toEqual({ type: "wait" });
  });

  it("ignores tapRejected after a plain send", () => {
    const first = next(initialState(p), observation([]));
    expect(next(first.state, observation([], { tapRejected: true })).action).toEqual({ type: "wait" });
  });
});

describe("a Knockout", () => {
  const knockout = (interrupts: PersonaEntry["interrupts"]) =>
    persona({
      answers: { zone: [reply("Centro")], driver_license: [reply("No tengo")] },
      interrupts,
      expect: { stage: "driver_license", status: "closed", outcome: "knockout", events: ["knockout"] },
    });
  const script: Say[][] = [["¿Cuál es tu zona?"], ["¿Tienes licencia?"], ["Necesitamos licencia vigente. Escríbenos cuando la tengas."], ["Hecho: el equipo revisará la decisión."]];

  it("fires after_knockout once, waits for its reply, then stops with knockout", () => {
    const run = drive(knockout([{ at: "after_knockout", advance: [], say: reply("revisión"), then: "stop" }]), script);
    expect(sends(run)).toEqual(["Hola", "Centro", "No tengo", "revisión"]);
    expect(run.actions.filter((a) => a.purpose === "interrupt:after_knockout")).toHaveLength(1);
    expect(run.stop).toMatchObject({ reason: "knockout" });
  });

  it("stops at once when the persona has no after_knockout interrupt", () => {
    const run = drive(knockout([]), script);
    expect(sends(run)).toEqual(["Hola", "Centro", "No tengo"]);
    expect(run.stop).toMatchObject({ reason: "knockout" });
  });

  it("is a Knockout only after a Knock-out field and a message that asks nothing", () => {
    // The agent asks again about the license: a re-ask, not a Knockout.
    const reask = drive(knockout([]), [["¿Cuál es tu zona?"], ["¿Tienes licencia?"], ["¿Puedes decirme si tienes licencia de manejo?"], ["Gracias."]]);
    expect(sends(reask).slice(0, 4)).toEqual(["Hola", "Centro", "No tengo", "No tengo"]);
    // The same text after a field that is not a Knock-out check is not one either.
    const other = persona({ answers: { full_name: [reply("Ana")] }, expect: { stage: "name", status: "closed", outcome: "knockout", events: [] } });
    expect(drive(other, [["¿Cuál es tu nombre?"], ["Necesitamos licencia vigente."]]).stop).toMatchObject({ reason: "completed" });
  });

  it("is also a Knockout when the persona only reacts to one", () => {
    const reacts = persona({
      answers: { zone: [reply("Centro")] },
      interrupts: [{ at: "after_knockout", advance: [], say: reply("revisión"), then: "stop" }],
    });
    const run = drive(reacts, [["¿Cuál es tu zona?"], ["Esa zona no está cubierta."], ["Hecho."]]);
    expect(sends(run)).toEqual(["Hola", "Centro", "revisión"]);
    expect(run.stop).toMatchObject({ reason: "knockout" });
  });
});

describe("interrupts at a field", () => {
  it("says the interrupt first, then answers the same question", () => {
    const p = persona({
      answers: { availability: [reply("tiempo completo")] },
      interrupts: [{ at: "availability", advance: [], say: reply("una duda: ¿hay uniforme?"), then: "continue" }],
    });
    const run = drive(p, [["¿Cuál es tu disponibilidad?"], ["Sí, lo damos."], ["Gracias."]]);
    expect(sends(run)).toEqual(["Hola", "una duda: ¿hay uniforme?", "tiempo completo"]);
    expect(run.actions.map((a) => a.purpose)).toEqual(["opening", "interrupt:availability", "answer:availability"]);
  });

  it("fires each interrupt once", () => {
    const p = persona({
      answers: { availability: [reply("tiempo completo")] },
      interrupts: [{ at: "availability", advance: [], say: reply("una duda"), then: "continue" }],
    });
    const ask = "¿Cuál es tu disponibilidad?";
    const run = drive(p, [[ask], ["Respuesta."], [ask], ["Gracias."]]);
    expect(sends(run)).toEqual(["Hola", "una duda", "tiempo completo", "tiempo completo"]);
  });

  it("asks the backout question's interrupt next and stops when it says then stop", () => {
    const p = persona({
      answers: { availability: [reply("tiempo completo")] },
      interrupts: [
        { at: "availability", advance: [], say: reply("mejor ya no me interesa"), then: "continue" },
        { at: "after_backout_prompt", advance: [], say: reply("Sí, seguro"), then: "stop" },
      ],
    });
    const run = drive(p, [["¿Cuál es tu disponibilidad?"], ["¿Seguro que quieres cancelar?"], ["Listo, cancelado."]]);
    expect(sends(run)).toEqual(["Hola", "mejor ya no me interesa", "Sí, seguro"]);
    expect(run.stop).toMatchObject({ reason: "stopped" });
  });

  it("taps from the last reply of the interrupt for after_backout_prompt when it picks an option", () => {
    const p = persona({
      answers: { availability: [reply("tiempo completo")] },
      interrupts: [
        { at: "availability", advance: [], say: reply("ya no"), then: "continue" },
        { at: "after_backout_prompt", advance: [], say: reply("Sí", { pick_option: 0 }), then: "stop" },
      ],
    });
    const run = drive(p, [["¿Cuál es tu disponibilidad?"], [{ text: "¿Seguro?", options: [["yes", "Sí"], ["no", "No"]] }], ["Listo."]]);
    expect(run.actions[2]).toMatchObject({ type: "tap", optionId: "yes", messageId: 4 });
  });
});

describe("the turn limit", () => {
  it("stops after 60 answers, as the driver does", () => {
    expect(MAX_TURNS).toBe(60);
    const p = persona({ answers: { zone: [reply("Puebla")] } });
    const run = drive(p, Array.from({ length: 100 }, () => ["¿En qué zona vives?"]));
    expect(run.actions).toHaveLength(61);
    expect(run.stop).toMatchObject({ reason: "max_turns" });
  });
});

describe("the terminal rule", () => {
  const p = persona({ answers: { zone: [reply("Puebla")] } });
  const ask: Say[][] = [["¿En qué zona vives?"], ["Gracias."]];
  const stops = (application: ApplicationState) => drive(p, ask, application).stop;

  it.each([
    [{ stage: "qualified", status: "open", outcome: null }, "stage qualified"],
    [{ stage: "approved", status: "open", outcome: null }, "stage approved"],
    [{ stage: "interview_booked", status: "open", outcome: null }, "stage interview_booked"],
    [{ stage: "zone", status: "closed", outcome: null }, "status closed"],
    [{ stage: "zone", status: "open", outcome: "withdrawn" }, "outcome withdrawn"],
  ])("stops on %j", (application, detail) => {
    expect(stops(application)).toEqual({ reason: "terminal", detail });
  });

  it("does not stop on an Application that is still being screened", () => {
    for (const application of [{ stage: "zone", status: "open", outcome: null }, { stage: null, status: null, outcome: null }]) {
      expect(stops(application)?.reason, JSON.stringify(application)).toBe("completed");
    }
    expect(drive(p, ask, null).stop?.reason).toBe("completed");
  });

  it("yields to the driver's own ends: a message that asks nothing is completed, not terminal", () => {
    const run = drive(persona(), [["Listo, gracias."]], { stage: "qualified", status: "open", outcome: null });
    expect(run.stop).toMatchObject({ reason: "completed" });
  });

  it("waits for a pending after_knockout interrupt to fire", () => {
    const q = persona({
      answers: { zone: [reply("Puebla")] },
      interrupts: [{ at: "after_knockout", advance: [], say: reply("revisión"), then: "stop" }],
    });
    const application = { stage: "qualified", status: "open", outcome: null };
    const held = drive(q, [["¿En qué zona vives?"]], application);
    expect(sends(held)).toEqual(["Hola", "Puebla"]);
    // Once it has fired, the rule applies.
    const messages = [chat({ id: 1, direction: "inbound", text: "Hola" }), chat({ id: 2, direction: "outbound", text: "¿En qué zona vives?" })];
    const waiting = next(initialState(q), observation([]));
    const result = next({ ...waiting.state, fired: [0] }, observation(messages, { application }));
    expect(result.action).toMatchObject({ type: "stop", stop: { reason: "terminal" } });
  });
});

describe("pace", () => {
  const confirmCorrection = PERSONAS.find((p) => p.id === "confirm_correction_mx")!;

  it("defaults to 30 characters per second and a 1.2 s pause", () => {
    expect(DEFAULT_PACE).toEqual({ charsPerSecond: 30, pauseSeconds: 1.2 });
  });

  it("schedules confirm_correction_mx in 20 s or less at the defaults", () => {
    const seconds = scheduleSeconds(confirmCorrection, DEFAULT_PACE);
    expect(seconds).toBeGreaterThan(10);
    expect(seconds).toBeLessThanOrEqual(20);
    expect(scheduleSeconds(confirmCorrection)).toBe(seconds);
  });

  it("counts every message once: a pause each plus the characters at the typing speed", () => {
    const p = persona({ opening: reply("abc"), answers: { zone: [reply("12345678")], full_name: [reply("ab"), reply("cd")] }, confirm: [plain("xx")] });
    // opening + zone + 2 names + 1 confirm = 5 messages, 3 + 8 + 4 + 2 = 17 characters.
    expect(scheduleSeconds(p, { charsPerSecond: 17, pauseSeconds: 2 })).toBeCloseTo(5 * 2 + 1, 6);
  });

  it("adds a plain yes when the persona has no confirm list, and counts interrupts", () => {
    const p = persona({ opening: reply("abcd"), interrupts: [{ at: "start", advance: [], say: reply("hi"), then: "continue" }] });
    // opening + interrupt + the default "Sí" = 3 messages, 4 + 2 + 2 characters.
    expect(scheduleSeconds(p, { charsPerSecond: 8, pauseSeconds: 1 })).toBeCloseTo(3 + 1, 6);
  });

  it("gives the fastest pace for zero settings", () => {
    expect(scheduleSeconds(confirmCorrection, { charsPerSecond: 0, pauseSeconds: 0 })).toBe(0);
    expect(scheduleSeconds(confirmCorrection, { charsPerSecond: 0, pauseSeconds: 1 })).toBeCloseTo(11, 6);
  });
});

describe("personas.json", () => {
  it("lists the field order the driver walks", () => {
    expect(FIELD_ORDER).toEqual(["full_name", "zone", "driver_license", "availability", "preferred_schedule", "experience_years", "experience_platforms", "start_date"]);
  });

  it("marks the personas the browser cannot play", () => {
    const unplayable = PERSONAS.filter((p) => !p.playable);
    expect(unplayable.length).toBeGreaterThan(0);
    for (const p of unplayable) expect(p.unplayable_reason, p.id).toMatch(/clock|voice_note|image/);
  });
});

// ---- blocks 3 and 4: the hook and the control ----------------------------------------------------
// PlayerControls is rendered inside StagePlayerContext with a fake session: a FakeChat stands in for
// the conversation (what `useConversation` returns) and records what the player asks of it. Timers are
// fake, so the pace is measured exactly.

const MX_OPENING = "Hola, quiero ser repartidor"; // confirm_correction_mx: 27 characters, 0.9 s at 30 cps
const ASK_ZONE = "¿En qué zona vives?";

class FakeChat {
  messages: ChatMessage[] = [];
  typing = false;
  runFinished: boolean | null = true;
  conversationId: string | null = null;
  draft = "";
  drafts: string[] = [];
  edits = 0;
  version = 0;
  nextId = 1;
  startOk = true;
  sendOk = true;
  tapOk = true;
  /** A backend whose pages never say `run_finished` or `agent_typing`. */
  silentRun = false;
  phone = "+525500000001";
  application: ApplicationSummary | null = null;
  readonly starts: ChatStartRequest[] = [];
  readonly takes: Array<[string | undefined, string | undefined]> = [];
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };
  getVersion = () => this.version;
  emit() {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  get answerableId(): number | null {
    const live = [...this.messages].reverse().find((m) => m.interactive);
    return live && !this.messages.some((m) => m.id > live.id && m.direction === "inbound") ? live.id : null;
  }

  newTake = vi.fn((country?: string, locale?: string) => {
    this.takes.push([country, locale]);
    this.phone = `+52550000${String(this.takes.length).padStart(4, "0")}`;
    this.messages = [];
    this.conversationId = null;
    this.emit();
    return this.phone;
  });

  start = vi.fn(async (request: ChatStartRequest) => {
    this.starts.push(request);
    if (!this.startOk) return false;
    this.conversationId = "wc_1";
    this.runFinished = this.silentRun ? null : true;
    this.emit();
    return true;
  });

  send = vi.fn(async (text: string) => {
    if (!this.sendOk) return false;
    this.pushInbound(text);
    return true;
  });

  tap = vi.fn(async (message: ChatMessage, optionId: string) => {
    if (!this.tapOk) return false;
    const label = message.interactive?.options.find((o) => o.id === optionId)?.label ?? optionId;
    this.pushInbound(label, { message_id: message.id, option_id: optionId });
    return true;
  });

  setDraft = (text: string) => {
    this.draft = text;
    this.drafts.push(text);
    this.emit();
  };

  /** The Operator types in the composer. */
  type(text: string) {
    this.draft = text;
    this.edits += 1;
    this.emit();
  }

  pushInbound(text: string, replyTo?: { message_id: number; option_id: string }) {
    this.messages = [...this.messages, chat({ id: this.nextId++, direction: "inbound", text, replyTo })];
    if (!this.silentRun) {
      this.runFinished = false;
      this.typing = true;
    }
    this.emit();
  }

  /** The agent's reply to the last Candidate message, and its run ends. */
  agent(...says: Say[]) {
    this.pushAgentOnly(...says);
    this.runFinished = true;
    this.typing = false;
    this.emit();
  }

  pushAgentOnly(...says: Say[]) {
    for (const say of says) {
      this.messages = [
        ...this.messages,
        typeof say === "string" ? chat({ id: this.nextId++, direction: "outbound", text: say }) : chat({ id: this.nextId++, direction: "outbound", text: say.text, options: say.options }),
      ];
    }
    this.emit();
  }

  conversation(): Conversation {
    return {
      conversationId: this.conversationId,
      messages: this.messages,
      typing: this.typing,
      runFinished: this.runFinished,
      error: null,
      personaName: null,
      language: null,
      busy: false,
      answerableId: this.answerableId,
      sent: this.messages.filter((m) => m.direction === "inbound").length,
      start: this.start,
      resume: () => undefined,
      send: this.send,
      tap: this.tap,
      reset: () => undefined,
    };
  }
}

interface Probe {
  available: boolean;
  isLoading: boolean;
}

function FakeStage({ chat: fake, probe }: { chat: FakeChat; probe: Probe }) {
  useSyncExternalStore(fake.subscribe, fake.getVersion);
  const value: StagePlayerValue = {
    session: {
      phone: fake.phone,
      country: "MX",
      locale: "es-MX",
      conversation: fake.conversation(),
      application: { application: fake.application, started: fake.application !== null, isError: false, error: null, retry: () => undefined },
      newTake: fake.newTake as StagePlayerValue["session"]["newTake"],
    },
    setDraft: fake.setDraft,
    composerEdits: fake.edits,
    probe,
  };
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return createElement(
    QueryClientProvider,
    { client },
    createElement(
      StagePlayerContext.Provider,
      { value },
      createElement(PlayerControls, null),
      createElement("input", { "aria-label": "Composer", "data-testid": "composer", value: fake.draft, onChange: (e: { target: { value: string } }) => fake.type(e.target.value) }),
    ),
  );
}

const SIM: Probe = { available: true, isLoading: false };
const LIVE: Probe = { available: false, isLoading: false };

function mount(probe: Probe = SIM) {
  const fake = new FakeChat();
  const view = render(createElement(FakeStage, { chat: fake, probe }));
  return { fake, ...view };
}

const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const picker = () => screen.getByRole("combobox", { name: "Persona" }) as HTMLSelectElement;
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
const statusText = () => screen.getByTestId("player-status").textContent;
const decisionText = () => screen.getByTestId("player-decision").textContent;
const composer = () => screen.getByTestId("composer") as HTMLInputElement;

async function choose(id: string) {
  fireEvent.change(picker(), { target: { value: id } });
  await tick(0);
}
async function press(name: string) {
  fireEvent.click(button(name));
  await tick(0);
}
async function setPace(speed: string, pause: string) {
  fireEvent.change(screen.getByLabelText("Characters per second"), { target: { value: speed } });
  fireEvent.change(screen.getByLabelText("Pause before each message (seconds)"), { target: { value: pause } });
  await tick(0);
}
/** Plays `id` at the fastest pace as far as the first Candidate message. */
async function playFast(id: string, probe: Probe = SIM) {
  const view = mount(probe);
  await setPace("0", "0");
  await choose(id);
  await press("Play");
  return view;
}

describe("the player control", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
  });

  describe("idle", () => {
    it("starts with no persona chosen: Play, Pause and Step are off, and there is no heading or region", () => {
      mount();
      expect(picker().value).toBe("");
      expect(button("Play")).toBeDisabled();
      expect(button("Pause")).toBeDisabled();
      expect(button("Step")).toBeDisabled();
      expect(statusText()).toBe("Choose a persona to play");
      expect(decisionText()).toBe("");
      expect(screen.queryAllByRole("heading")).toHaveLength(0);
      expect(screen.queryAllByRole("region")).toHaveLength(0);
    });

    it("lists every playable persona, and the others disabled with the reason", () => {
      mount();
      const options = [...picker().options];
      for (const p of PERSONAS) {
        const option = options.find((o) => o.value === p.id)!;
        expect(option, p.id).toBeDefined();
        expect(option.disabled, p.id).toBe(!p.playable);
        if (!p.playable) expect(option.textContent, p.id).toContain(p.unplayable_reason);
      }
      expect(options[0].value).toBe("");
    });

    it("enables Play for a playable persona and ignores an unavailable one", async () => {
      mount();
      await choose("withdrawn_mx");
      expect(picker().value).toBe("");
      expect(button("Play")).toBeDisabled();
      await choose("confirm_correction_mx");
      expect(picker().value).toBe("confirm_correction_mx");
      expect(button("Play")).toBeEnabled();
      expect(button("Step")).toBeEnabled();
      expect(button("Pause")).toBeDisabled();
    });
  });

  describe("starting a take", () => {
    it.each([
      ["confirm_correction_mx", "MX", "es-MX"],
      ["language_one_word_en", "MX", "en"],
      ["qualified_es", "ES", "es-ES"],
    ])("Play from idle starts a take with the country and locale of %s", async (id, country, locale) => {
      const { fake } = mount();
      await choose(id);
      await press("Play");
      expect(fake.takes).toEqual([[country, locale]]);
      expect(fake.starts).toEqual([{ country, locale, phone: fake.phone, client: "demo", persona_id: id }]);
      expect(statusText()).toBe("Running");
    });

    it("Step from idle starts a take that carries the chosen persona's id and client demo", async () => {
      const { fake } = mount();
      await choose("qualified_es");
      await press("Step");
      expect(fake.starts).toEqual([{ country: "ES", locale: "es-ES", phone: fake.phone, client: "demo", persona_id: "qualified_es" }]);
    });

    it("disables Play and Step while a run is active, and Pause turns them back on", async () => {
      mount();
      await choose("confirm_correction_mx");
      await press("Play");
      expect(button("Play")).toBeDisabled();
      expect(button("Step")).toBeDisabled();
      expect(button("Pause")).toBeEnabled();
      expect(picker()).toBeDisabled();
      await press("Pause");
      expect(statusText()).toBe("Paused");
      expect(button("Play")).toBeEnabled();
      expect(button("Step")).toBeEnabled();
      expect(button("Pause")).toBeDisabled();
    });
  });

  describe("pace", () => {
    it("pauses 1.2 s, then types 30 characters a second, then sends", async () => {
      const { fake } = mount();
      await choose("confirm_correction_mx");
      await press("Play");
      await tick(1190);
      expect(fake.send).not.toHaveBeenCalled();
      expect(composer().value).toBe("");
      await tick(300);
      expect(composer().value.length).toBeGreaterThan(0);
      expect(composer().value.length).toBeLessThan(MX_OPENING.length);
      expect(MX_OPENING.startsWith(composer().value)).toBe(true);
      expect(statusText()).toBe("Running");
      await tick(700);
      expect(fake.send).toHaveBeenCalledTimes(1);
      expect(fake.send).toHaveBeenCalledWith(MX_OPENING);
      expect(composer().value).toBe("");
    });

    it("shows the whole text at once under prefers-reduced-motion: reduce", async () => {
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }));
      const { fake } = mount();
      await choose("confirm_correction_mx");
      await press("Play");
      await tick(1190);
      expect(fake.send).not.toHaveBeenCalled();
      await tick(20);
      expect(fake.send).toHaveBeenCalledWith(MX_OPENING);
      expect(fake.drafts.filter((d) => d !== "")).toEqual([MX_OPENING]);
    });

    it("sends at once when both settings are zero", async () => {
      const { fake } = mount();
      await setPace("0", "0");
      await choose("confirm_correction_mx");
      await press("Play");
      expect(fake.send).toHaveBeenCalledWith(MX_OPENING);
      expect(fake.drafts.filter((d) => d !== "")).toEqual([MX_OPENING]);
    });

    it("keeps the pause when only the typing speed is zero", async () => {
      const { fake } = mount();
      await setPace("0", "2");
      await choose("confirm_correction_mx");
      await press("Play");
      await tick(1900);
      expect(fake.send).not.toHaveBeenCalled();
      await tick(200);
      expect(fake.send).toHaveBeenCalledWith(MX_OPENING);
    });
  });

  describe("the last decision", () => {
    it("says what it detected and which reply it sent", async () => {
      const { fake } = await playFast("no_license_mx");
      expect(decisionText()).toBe("sent the opening");
      await act(async () => fake.agent(ASK_ZONE));
      await tick(0);
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
      expect(decisionText()).toBe("detected zone, sent reply 1 of 2");
      await act(async () => fake.agent(ASK_ZONE));
      await tick(0);
      expect(fake.send).toHaveBeenLastCalledWith("Centro, Puebla");
      expect(decisionText()).toBe("detected zone, sent reply 2 of 2");
    });
  });

  describe("taking over by hand", () => {
    it("pauses on a keystroke in the composer and moves no counter", async () => {
      const { fake } = await playFast("no_license_mx");
      expect(statusText()).toBe("Running");
      fireEvent.change(composer(), { target: { value: "x" } });
      await tick(0);
      expect(statusText()).toBe("Paused: you typed in the composer");
      expect(decisionText()).toBe("sent the opening");
      await act(async () => fake.agent(ASK_ZONE));
      await tick(2000);
      expect(fake.send).toHaveBeenCalledTimes(1);
      await press("Play");
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
      expect(decisionText()).toBe("detected zone, sent reply 1 of 2");
    });

    it("pauses on a message sent by hand", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => fake.pushInbound("hola, soy yo"));
      await tick(0);
      expect(statusText()).toBe("Paused: you sent a message");
      expect(decisionText()).toBe("sent the opening");
      expect(fake.send).toHaveBeenCalledTimes(1);
    });

    it("pauses on an option tapped by hand", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => fake.agent({ text: "¿Tienes licencia?", options: [["yes", "Sí"], ["no", "No"]] }));
      await act(async () => fake.pushInbound("Sí", { message_id: 2, option_id: "yes" }));
      await tick(0);
      expect(statusText()).toBe("Paused: you tapped a reply");
    });

    it("does not pause for its own typing, sends or taps", async () => {
      const { fake } = mount();
      await choose("confirm_correction_mx");
      await press("Play");
      await tick(2200);
      expect(fake.send).toHaveBeenCalledTimes(1);
      expect(statusText()).toBe("Running");
    });
  });

  describe("waiting for the agent", () => {
    it("pauses with a reason when no settled reply comes in 60 s", async () => {
      await playFast("no_license_mx");
      await tick(59_000);
      expect(statusText()).toBe("Running");
      await tick(1_100);
      expect(statusText()).toBe("Paused: no reply for 60 seconds");
    });

    it("reads a null runFinished as settled once typing has been off for a second", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => {
        fake.runFinished = null;
        fake.typing = false;
        fake.pushAgentOnly(ASK_ZONE);
      });
      await tick(900);
      expect(fake.send).toHaveBeenCalledTimes(1);
      await tick(200);
      expect(fake.send).toHaveBeenCalledTimes(2);
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
    });

    it("does not read a reply while the run is still going", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => fake.pushAgentOnly(ASK_ZONE));
      await tick(3000);
      expect(fake.runFinished).toBe(false);
      expect(fake.send).toHaveBeenCalledTimes(1);
    });
  });

  describe("a tap", () => {
    const zones: Say = { text: "Encontré varias zonas. ¿Cuál es la tuya?", options: [["zone_ang", "Angelópolis, Puebla"], ["zone_centro", "Centro, Puebla"], ["zone_paz", "La Paz, Puebla"]] };

    it("taps the option on the live message", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => fake.agent(zones));
      await tick(0);
      expect(fake.tap).toHaveBeenCalledTimes(1);
      expect(fake.tap.mock.calls[0][1]).toBe("zone_centro");
      expect(fake.send).toHaveBeenCalledTimes(1);
    });

    it("sends the text without tapping when the prompting options are not the live ones", async () => {
      const { fake } = await playFast("no_license_mx");
      // Two interactive messages: only the later one is live, but the zone question is the earlier.
      await act(async () => fake.agent(zones, { text: "¿Seguro?", options: [["yes", "Sí"], ["no", "No"]] }));
      await tick(10);
      expect(fake.tap).not.toHaveBeenCalled();
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
      expect(decisionText()).toBe("tap not accepted, sent the reply text");
    });

    it("sends the reply's text when the tap is refused", async () => {
      const { fake } = await playFast("no_license_mx");
      fake.tapOk = false;
      await act(async () => fake.agent(zones));
      await tick(10);
      expect(fake.tap).toHaveBeenCalledTimes(1);
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
      expect(decisionText()).toBe("tap not accepted, sent the reply text");
      expect(statusText()).toBe("Running");
    });
  });

  describe("a step", () => {
    it("sends one message and pauses, again and again", async () => {
      const { fake } = mount();
      await setPace("0", "0");
      await choose("no_license_mx");
      await press("Step");
      expect(fake.send).toHaveBeenCalledTimes(1);
      expect(statusText()).toBe("Paused: one message sent");
      await act(async () => fake.agent(ASK_ZONE));
      await tick(3000);
      expect(fake.send).toHaveBeenCalledTimes(1);
      await press("Step");
      expect(fake.send).toHaveBeenCalledTimes(2);
      expect(fake.send).toHaveBeenLastCalledWith("Puebla");
      expect(statusText()).toBe("Paused: one message sent");
      await press("Play");
      expect(statusText()).toBe("Running");
    });
  });

  describe("the end of a run", () => {
    it("shows why it stopped, and Play starts a new take", async () => {
      const { fake } = await playFast("no_license_mx");
      await act(async () => fake.agent("Gracias, el equipo te escribirá."));
      await tick(0);
      expect(statusText()).toBe("Stopped: the agent asked nothing more");
      expect(button("Play")).toBeEnabled();
      expect(button("Pause")).toBeDisabled();
      expect(picker()).toBeEnabled();
      await press("Play");
      expect(fake.starts).toHaveLength(2);
    });

    it("stops on a terminal Application, read from the detail", async () => {
      const mock = mockApi({ "GET /api/applications/:id": detail({ id: "app_x", stage: "qualified", status: "open", outcome: null }) });
      const { fake } = await playFast("qualified_es");
      // The lookup finds the Application (still at greeting); the detail query reads where it has got to.
      fake.application = summary({ id: "app_x", stage: "greeting" });
      await act(async () => fake.emit());
      await tick(50);
      expect(mock.find("GET", "/api/applications/app_x").length).toBeGreaterThan(0);
      await act(async () => fake.agent("¿Cuál es tu nombre?"));
      await tick(50);
      expect(statusText()).toBe("Stopped: the Application reached stage qualified");
    });
  });

  describe("an error", () => {
    it("shows it with Retry when the take cannot start, and Retry starts it again", async () => {
      const { fake } = mount();
      fake.startOk = false;
      await choose("confirm_correction_mx");
      await press("Play");
      expect(statusText()).toBe("Could not start the take");
      expect(button("Retry")).toBeEnabled();
      fake.startOk = true;
      await press("Retry");
      expect(fake.starts).toHaveLength(2);
      expect(statusText()).toBe("Running");
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    });

    it("shows it with Retry when a message is refused, and Retry sends it again", async () => {
      const { fake } = mount();
      await setPace("0", "0");
      fake.sendOk = false;
      await choose("confirm_correction_mx");
      await press("Play");
      expect(statusText()).toBe("Could not send the message");
      expect(fake.send).toHaveBeenCalledTimes(1);
      fake.sendOk = true;
      await press("Retry");
      expect(fake.send).toHaveBeenCalledTimes(2);
      expect(fake.send).toHaveBeenLastCalledWith(MX_OPENING);
      expect(statusText()).toBe("Running");
    });
  });

  describe("a stack with no sim probe", () => {
    it("shows the cost line, and asks before a second take", async () => {
      const { fake } = await playFast("no_license_mx", LIVE);
      expect(screen.getByText("About $0.01 per conversation")).toBeInTheDocument();
      expect(fake.starts).toHaveLength(1);
      await act(async () => fake.agent("Gracias, el equipo te escribirá."));
      await tick(0);
      await press("Play");
      expect(fake.starts).toHaveLength(1);
      expect(button("Start another take")).toBeEnabled();
      await press("Cancel");
      expect(screen.queryByRole("button", { name: "Start another take" })).toBeNull();
      expect(fake.starts).toHaveLength(1);
      await press("Play");
      await press("Start another take");
      expect(fake.starts).toHaveLength(2);
      expect(statusText()).toBe("Running");
    });

    it("asks nothing and shows no cost on a sim stack", async () => {
      const { fake } = await playFast("no_license_mx", SIM);
      expect(screen.queryByText("About $0.01 per conversation")).toBeNull();
      await act(async () => fake.agent("Gracias, el equipo te escribirá."));
      await tick(0);
      await press("Play");
      expect(screen.queryByRole("button", { name: "Start another take" })).toBeNull();
      expect(fake.starts).toHaveLength(2);
    });

    it("waits for the probe before it lets a take start", async () => {
      mount({ available: false, isLoading: true });
      await choose("confirm_correction_mx");
      expect(button("Play")).toBeDisabled();
      expect(screen.queryByText("About $0.01 per conversation")).toBeNull();
    });
  });

  describe("when the page goes away", () => {
    it("does nothing after unmount when a send resolves late", async () => {
      const { fake, unmount } = mount();
      let finish: (ok: boolean) => void = () => undefined;
      fake.send.mockImplementationOnce(() => new Promise<boolean>((resolve) => (finish = resolve)));
      await setPace("0", "0");
      await choose("no_license_mx");
      await press("Play");
      expect(fake.send).toHaveBeenCalledTimes(1);
      unmount();
      // What is left is the query cache's own; the player must add none (no 60 s reply timer).
      const left = vi.getTimerCount();
      finish(true);
      await tick(0);
      expect(vi.getTimerCount()).toBe(left);
    });

    it("does nothing after unmount when a take resolves late", async () => {
      const { fake, unmount } = mount();
      let finish: (ok: boolean) => void = () => undefined;
      // The conversation is announced and rendered before `start` returns, so the player has what it
      // needs to move on the moment it does.
      fake.start.mockImplementationOnce(async () => {
        fake.conversationId = "wc_1";
        fake.emit();
        return new Promise<boolean>((resolve) => (finish = resolve));
      });
      await choose("no_license_mx");
      await press("Play");
      unmount();
      const left = vi.getTimerCount();
      finish(true);
      await tick(0);
      expect(vi.getTimerCount()).toBe(left);
    });

    it("does nothing after unmount when a tap resolves late", async () => {
      const { fake, unmount } = mount();
      let finish: (ok: boolean) => void = () => undefined;
      fake.tap.mockImplementationOnce(() => new Promise<boolean>((resolve) => (finish = resolve)));
      await setPace("0", "0");
      await choose("no_license_mx");
      await press("Play");
      await act(async () =>
        fake.agent({ text: "Encontré varias zonas. ¿Cuál es la tuya?", options: [["zone_ang", "Angelópolis, Puebla"], ["zone_centro", "Centro, Puebla"]] }),
      );
      await tick(10);
      expect(fake.tap).toHaveBeenCalledTimes(1);
      unmount();
      const left = vi.getTimerCount();
      finish(true);
      await tick(0);
      expect(vi.getTimerCount()).toBe(left);
    });
  });

  describe("a run that never says it has finished", () => {
    it("counts the quiet again after every send", async () => {
      const fake = new FakeChat();
      fake.silentRun = true;
      render(createElement(FakeStage, { chat: fake, probe: SIM }));
      await setPace("0", "3");
      await choose("no_license_mx");
      await press("Play");
      // The page has been quiet for longer than a second when the opening goes out, at 3 s.
      await tick(3010);
      expect(fake.send).toHaveBeenCalledTimes(1);
      await act(async () => fake.pushAgentOnly(ASK_ZONE));
      await tick(900);
      expect(decisionText()).toBe("sent the opening");
      await tick(200);
      expect(decisionText()).toBe("detected zone, sent reply 1 of 2");
    });
  });

  describe("a take the player did not start", () => {
    it("pauses when the Operator starts a new one, and Play then starts a fresh take", async () => {
      const { fake } = await playFast("no_license_mx");
      expect(fake.takes).toEqual([["MX", "es-MX"]]);
      // The start card's country picker: a new Take by hand, mid-run.
      await act(async () => void fake.newTake("ES"));
      await tick(0);
      expect(statusText()).toBe("Paused: a new take was started");
      expect(fake.send).toHaveBeenCalledTimes(1);
      await tick(5000);
      expect(fake.send).toHaveBeenCalledTimes(1);
      await press("Play");
      expect(fake.takes).toEqual([["MX", "es-MX"], ["ES", undefined], ["MX", "es-MX"]]);
      expect(fake.starts).toHaveLength(2);
      expect(statusText()).toBe("Running");
    });

    it("does not mistake its own take for one", async () => {
      await playFast("no_license_mx");
      await tick(2000);
      expect(statusText()).toBe("Running");
    });
  });

  describe("the keyboard", () => {
    it("reaches every control, each with a name, in reading order", async () => {
      mount();
      await choose("confirm_correction_mx");
      const controls = [
        picker(),
        button("Play"),
        button("Pause"),
        button("Step"),
        screen.getByLabelText("Characters per second"),
        screen.getByLabelText("Pause before each message (seconds)"),
      ];
      for (const control of controls) {
        expect(control.tabIndex, control.outerHTML).toBeGreaterThanOrEqual(0);
        if (!(control as HTMLButtonElement).disabled) {
          control.focus();
          expect(document.activeElement).toBe(control);
        }
      }
      const order = controls.map((c) => c.compareDocumentPosition(controls[controls.indexOf(c) + 1] ?? c));
      for (const position of order.slice(0, -1)) expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(button("Play").tagName).toBe("BUTTON");
      expect(picker().tagName).toBe("SELECT");
    });

    it("works from the keyboard: a focused Play starts the take on click, Enter and Space map to click on buttons", async () => {
      const { fake } = mount();
      await choose("confirm_correction_mx");
      button("Play").focus();
      expect(document.activeElement).toBe(button("Play"));
      fireEvent.click(document.activeElement as HTMLElement);
      await tick(0);
      expect(fake.starts).toHaveLength(1);
      button("Pause").focus();
      expect(document.activeElement).toBe(button("Pause"));
    });
  });
});
