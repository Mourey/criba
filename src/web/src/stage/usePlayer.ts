import { useContext, useEffect, useState, useSyncExternalStore } from "react";
import type { ChatMessage } from "../api/types.ts";
import type { Conversation } from "../api/useConversation.ts";
import { DEFAULT_PACE, initialState, next, PERSONAS, type ApplicationState, type Decision, type PersonaEntry, type PlayerState, type SendAction, type Stop, type TapAction } from "./playback.ts";
import type { PauseReason, PlayerErrorKind } from "./playerCopy.ts";
import { StagePlayerContext, useStageDetail, type StagePlayerValue } from "./useStageSession.ts";

// The player's timers and the page's side of it. playback.ts decides what to send; this file carries it
// out: it waits the pause, types into the composer at the set speed, sends or taps through the stage
// session's conversation, and watches for the Operator taking over. It is the only place with timers.
//
// It lives in a plain object behind useSyncExternalStore, not in component state: the timers and the
// requests outlive any one render, and one place holds every rule about them. The component feeds it the
// latest inputs after each render (`sync`) and reads an immutable `PlayerView` back.

/** No settled reply for this long pauses the player with a reason. */
export const REPLY_TIMEOUT_MS = 60_000;
/** A run that never says it has finished (`runFinished` null) counts as settled once typing has been off this long. */
export const QUIET_MS = 1000;

export type PlayerStatus = "idle" | "running" | "paused" | "stopped" | "error";

export interface PlayerView {
  /** The persona chosen, or "" for none. */
  personaId: string;
  status: PlayerStatus;
  pauseReason: PauseReason | null;
  errorKind: PlayerErrorKind | null;
  stop: Stop | null;
  /** The player's last decision, for the control's line. */
  decision: Decision | null;
  /** The pace settings as typed. A blank or invalid one reads as the default; 0 is the fastest. */
  speedText: string;
  pauseText: string;
  /** A second take on a stack with no sim probe is waiting for a yes. */
  confirming: boolean;
  /** The stack has no sim probe: every take spends real money. */
  showCost: boolean;
  canChoose: boolean;
  canPlay: boolean;
  canStep: boolean;
  canPause: boolean;
  /** A new Take can be made: no run is going and no confirm is open. */
  canNewTake: boolean;
  /** Prepare take can start: as `canNewTake`, with the sim probe answered and its persona playable. */
  canPrepare: boolean;
  /** Prepare take's progress: Candidate messages sent of the persona's scripted sends; null otherwise. */
  prepare: { done: number; total: number } | null;
}

/** The persona Prepare take plays: the Knockout Take, before the main one. */
export const PREPARE_PERSONA = "no_license_mx";

/** How many messages a persona's script can send: the opening, each reply, the confirm replies or a plain yes, each interrupt that speaks. */
export function scriptedSends(persona: PersonaEntry): number {
  const answers = Object.values(persona.answers).reduce((sum, list) => sum + list.length, 0);
  return 1 + answers + Math.max(persona.confirm.length, 1) + persona.interrupts.filter((interrupt) => interrupt.say).length;
}

type Timer = ReturnType<typeof setTimeout>;

interface Inputs {
  conversation: Conversation;
  newTake: StagePlayerValue["session"]["newTake"];
  setDraft: (text: string) => void;
  composerEdits: number;
  probe: StagePlayerValue["probe"];
  /** The phone of the take on the stage: a change the player did not make is the Operator's. */
  phone: string;
  application: ApplicationState | null;
}

const INITIAL: PlayerView = {
  personaId: "",
  status: "idle",
  pauseReason: null,
  errorKind: null,
  stop: null,
  decision: null,
  speedText: String(DEFAULT_PACE.charsPerSecond),
  pauseText: String(DEFAULT_PACE.pauseSeconds),
  confirming: false,
  showCost: false,
  canChoose: true,
  canPlay: false,
  canStep: false,
  canPause: false,
  canNewTake: true,
  canPrepare: false,
  prepare: null,
};

const countInbound = (messages: readonly ChatMessage[]): number => messages.filter((m) => m.direction === "inbound").length;

/** A blank, negative or invalid setting reads as the default; zero is a real setting. */
function parseSetting(text: string, fallback: number): number {
  const value = Number.parseFloat(text);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

/** `runFinished` is null before the first page, and absent from a backend that never says. */
const runUnknown = (conversation: Conversation): boolean => conversation.runFinished === null || conversation.runFinished === undefined;

const prefersReducedMotion = (): boolean => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const personaOf = (id: string): PersonaEntry | undefined => PERSONAS.find((p) => p.id === id);

function shallowEqual(a: PlayerView, b: PlayerView): boolean {
  return (Object.keys(a) as Array<keyof PlayerView>).every((key) => a[key] === b[key]);
}

class PlayerEngine {
  private view: PlayerView = INITIAL;
  private listeners = new Set<() => void>();
  private inputs: Inputs | null = null;
  private machine: PlayerState | null = null;
  /** The action decided but not yet carried out (the pause, the typing or the request is under way). */
  private pending: SendAction | TapAction | null = null;
  private tapRejected = false;
  /** Candidate messages that are ours, counting the one being sent: more than that is the Operator's. */
  private expected = 0;
  private quiet = false;
  private stepOnce = false;
  private confirmStep = false;
  private takes = 0;
  private inFlight = false;
  private edits: number | null = null;
  private takePhone: string | null = null;
  private disposed = false;
  /** The phone New take made, kept for the next Play. */
  private readyPhone: string | null = null;
  /** Messages the player has delivered in this run (for Prepare take's progress). */
  private delivered = 0;
  private prep: { personaId: string; speedText: string; pauseText: string; total: number; started: boolean } | null = null;
  private timer: Timer | null = null;
  private replyTimer: Timer | null = null;
  private quietTimer: Timer | null = null;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };
  getSnapshot = (): PlayerView => this.view;

  // ---- the view ----------------------------------------------------------------------------------

  private set(patch: Partial<PlayerView>): void {
    const merged = { ...this.view, ...patch };
    const persona = personaOf(merged.personaId);
    const probeLoading = this.inputs?.probe.isLoading ?? false;
    const idleish = merged.status === "idle" || merged.status === "paused" || merged.status === "stopped";
    merged.showCost = this.inputs !== null && !this.inputs.probe.isLoading && !this.inputs.probe.available;
    merged.canPlay = persona?.playable === true && idleish && !probeLoading && !merged.confirming;
    merged.canStep = merged.canPlay;
    merged.canPause = merged.status === "running";
    merged.canChoose = merged.status !== "running";
    merged.canNewTake = merged.status !== "running" && !merged.confirming;
    merged.canPrepare = merged.canNewTake && !probeLoading && personaOf(PREPARE_PERSONA)?.playable === true;
    if (shallowEqual(this.view, merged)) return;
    this.view = merged;
    for (const listener of this.listeners) listener();
    this.settlePrepare();
  }

  /** Prepare take ends when its run leaves "running" for any reason: the presenter's pace comes back. */
  private settlePrepare(): void {
    const prep = this.prep;
    if (!prep || !prep.started || this.view.status === "running") return;
    this.prep = null;
    this.set({ speedText: prep.speedText, pauseText: prep.pauseText, prepare: null });
    // A finished run gives the picker back to the presenter's persona; a paused or failed one stays as it is.
    if (this.view.status === "stopped") this.choose(prep.personaId);
  }

  // ---- inputs ------------------------------------------------------------------------------------

  /** Takes the latest page state after a render, notices the Operator, and moves the run on. */
  sync(inputs: Inputs): void {
    this.inputs = inputs;
    this.disposed = false;
    if (this.edits === null) this.edits = inputs.composerEdits;
    else if (inputs.composerEdits !== this.edits) {
      this.edits = inputs.composerEdits;
      this.pause("composer");
    }
    this.watchTake(inputs.phone);
    this.watchInbound(inputs.conversation.messages);
    this.watchQuiet(inputs.conversation);
    this.set({});
    this.advance();
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.clearReplyTimer();
    this.clearQuietTimer();
  }

  /** A new take that the player did not start (the start card's country picker) is the Operator's: pause. */
  private watchTake(phone: string): void {
    if (!this.machine || this.takePhone === null || phone === this.takePhone) return;
    if (this.view.status !== "running" && this.view.status !== "paused") return;
    this.clearTimer();
    this.clearReplyTimer();
    this.machine = null;
    this.pending = null;
    this.stepOnce = false;
    this.set({ status: "paused", pauseReason: "new_take" });
  }

  /** A Candidate message that is not ours is the Operator's hand: pause, and leave every counter alone. */
  private watchInbound(messages: readonly ChatMessage[]): void {
    const count = countInbound(messages);
    if (this.view.status === "running") {
      if (count > this.expected) {
        const newest = [...messages].reverse().find((m) => m.direction === "inbound");
        this.expected = count;
        this.pause(newest?.reply_to ? "hand_tap" : "hand_send");
      }
    } else if (!this.inFlight) {
      this.expected = Math.max(this.expected, count);
    }
  }

  /** `runFinished` null (a backend that never says) counts as settled once typing has been off for a second. */
  private watchQuiet(conversation: Conversation): void {
    if (!runUnknown(conversation) || conversation.typing) {
      this.clearQuietTimer();
      this.quiet = false;
    } else if (!this.quiet && this.quietTimer === null) {
      this.quietTimer = setTimeout(() => {
        this.quietTimer = null;
        this.quiet = true;
        this.advance();
      }, QUIET_MS);
    }
  }

  // ---- the run -----------------------------------------------------------------------------------

  private advance(): void {
    const inputs = this.inputs;
    if (this.view.status !== "running" || this.inFlight || this.timer !== null || !this.machine || !inputs) return;
    const conversation = inputs.conversation;
    if (conversation.conversationId === null) return;
    if (this.pending) {
      this.execute(this.pending);
      return;
    }
    const settled = conversation.runFinished === true || (runUnknown(conversation) && !conversation.typing && this.quiet);
    const { state, action } = next(this.machine, {
      messages: conversation.messages,
      settled,
      application: inputs.application,
      tapRejected: this.tapRejected,
    });
    this.machine = state;
    this.tapRejected = false;
    if (action.type === "wait") {
      this.armReplyTimer();
      return;
    }
    this.clearReplyTimer();
    if (action.type === "stop") {
      this.set({ status: "stopped", stop: action.stop });
      return;
    }
    this.pending = action;
    this.set({ decision: action.decision });
    this.execute(action);
  }

  /** The pause before a message, then its typing or its tap. */
  private execute(action: SendAction | TapAction): void {
    this.clearTimer();
    const pauseMs = parseSetting(this.view.pauseText, DEFAULT_PACE.pauseSeconds) * 1000;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (action.type === "tap") void this.doTap(action);
      else this.type(action);
    }, pauseMs);
  }

  private type(action: SendAction): void {
    const speed = parseSetting(this.view.speedText, DEFAULT_PACE.charsPerSecond);
    if (speed <= 0 || prefersReducedMotion()) {
      this.inputs?.setDraft(action.text);
      void this.doSend(action);
      return;
    }
    this.typeFrom(action, [...action.text], 1000 / speed, 1);
  }

  private typeFrom(action: SendAction, chars: string[], delay: number, count: number): void {
    this.timer = setTimeout(() => {
      this.timer = null;
      this.inputs?.setDraft(chars.slice(0, count).join(""));
      if (count >= chars.length) void this.doSend(action);
      else this.typeFrom(action, chars, delay, count + 1);
    }, delay);
  }

  private async doSend(action: SendAction): Promise<void> {
    const conversation = this.inputs?.conversation;
    if (!conversation) return;
    this.inFlight = true;
    this.expected += 1;
    const ok = await conversation.send(action.text);
    this.inFlight = false;
    if (this.disposed) return;
    this.inputs?.setDraft("");
    if (!ok) {
      this.expected -= 1;
      this.fail("send");
      return;
    }
    this.pending = null;
    this.afterDelivery();
  }

  private async doTap(action: TapAction): Promise<void> {
    const conversation = this.inputs?.conversation;
    if (!conversation) return;
    // Only the live options can be tapped. Anything else is a refused tap, and the reply goes as text.
    const message = conversation.messages.find((m) => m.id === action.messageId);
    let ok = false;
    if (message && conversation.answerableId === action.messageId) {
      this.inFlight = true;
      this.expected += 1;
      ok = await conversation.tap(message, action.optionId);
      this.inFlight = false;
      if (this.disposed) return;
      if (!ok) this.expected -= 1;
    }
    this.pending = null;
    if (!ok) {
      this.tapRejected = true;
      this.advance();
      return;
    }
    this.afterDelivery();
  }

  private afterDelivery(): void {
    this.delivered += 1;
    if (this.prep) this.set({ prepare: { done: this.delivered, total: this.prep.total } });
    // The next reply is judged quiet by what happens from now on, not by the wait before this message.
    this.quiet = false;
    this.clearQuietTimer();
    if (this.view.status !== "running") return;
    if (this.stepOnce) {
      this.stepOnce = false;
      this.pause("step");
      return;
    }
    this.advance();
  }

  private fail(kind: PlayerErrorKind): void {
    this.clearTimer();
    this.clearReplyTimer();
    if (this.view.status === "running") this.set({ status: "error", errorKind: kind });
  }

  private pause(reason: PauseReason): void {
    if (this.view.status !== "running") return;
    const typing = this.pending !== null && !this.inFlight;
    this.clearTimer();
    this.clearReplyTimer();
    this.stepOnce = false;
    // A half-typed line of ours does not stay in the composer, but the Operator's own text does.
    if (reason === "user" && typing) this.inputs?.setDraft("");
    if (this.inputs) this.expected = Math.max(this.expected, countInbound(this.inputs.conversation.messages));
    this.set({ status: "paused", pauseReason: reason });
  }

  private armReplyTimer(): void {
    if (this.replyTimer !== null) return;
    this.replyTimer = setTimeout(() => {
      this.replyTimer = null;
      this.pause("no_reply");
    }, REPLY_TIMEOUT_MS);
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private clearReplyTimer(): void {
    if (this.replyTimer !== null) clearTimeout(this.replyTimer);
    this.replyTimer = null;
  }

  private clearQuietTimer(): void {
    if (this.quietTimer !== null) clearTimeout(this.quietTimer);
    this.quietTimer = null;
  }

  // ---- commands ----------------------------------------------------------------------------------

  choose = (id: string): void => {
    const persona = personaOf(id);
    if (this.view.status === "running" || (id !== "" && !persona?.playable)) return;
    this.machine = null;
    this.pending = null;
    this.readyPhone = null;
    this.clearReplyTimer();
    this.set({ personaId: id, status: "idle", pauseReason: null, errorKind: null, stop: null, decision: null, confirming: false });
  };

  setPace = (patch: { speedText?: string; pauseText?: string }): void => this.set(patch);

  play = (): void => this.begin(false);
  step = (): void => this.begin(true);

  pauseNow = (): void => this.pause("user");

  /** A fresh phone in the chosen persona's country and locale (else the current country), ready for Play. */
  newTake = (): string | null => {
    const inputs = this.inputs;
    if (!inputs || !this.view.canNewTake) return null;
    const persona = personaOf(this.view.personaId);
    this.clearTimer();
    this.clearReplyTimer();
    this.machine = null;
    this.pending = null;
    this.stepOnce = false;
    const phone = persona ? inputs.newTake(persona.identity.country, persona.identity.locale) : inputs.newTake();
    this.readyPhone = phone;
    this.set({ status: "idle", pauseReason: null, errorKind: null, stop: null, decision: null, confirming: false });
    return phone;
  };

  /** Plays the Knockout persona to its end at the fastest pace; the presenter's pace and persona come back after. */
  prepare = (): void => {
    const persona = personaOf(PREPARE_PERSONA);
    if (!this.view.canPrepare || !persona) return;
    const prep = { personaId: this.view.personaId, speedText: this.view.speedText, pauseText: this.view.pauseText, total: scriptedSends(persona), started: false };
    this.choose(PREPARE_PERSONA);
    this.prep = prep;
    this.set({ speedText: "0", pauseText: "0" });
    this.begin(false);
  };

  /** Leaves whatever run was on the stage behind, idle, so a stored Take can be put in its place. */
  release = (): void => {
    if (this.view.status === "running") return;
    this.clearTimer();
    this.clearReplyTimer();
    this.machine = null;
    this.pending = null;
    this.readyPhone = null;
    this.stepOnce = false;
    this.set({ status: "idle", pauseReason: null, errorKind: null, stop: null, decision: null, confirming: false });
  };

  private begin(step: boolean): void {
    if (!this.view.canPlay) return;
    if (this.view.status === "paused" && this.machine) {
      this.stepOnce = step;
      if (this.inputs) this.expected = Math.max(this.expected, countInbound(this.inputs.conversation.messages));
      this.set({ status: "running", pauseReason: null });
      this.advance();
      return;
    }
    if (this.takes >= 1 && this.view.showCost) {
      this.confirmStep = step;
      this.set({ confirming: true });
      return;
    }
    void this.startTake(step, true);
  }

  confirm = (): void => {
    if (!this.view.confirming) return;
    this.set({ confirming: false });
    void this.startTake(this.confirmStep, true);
  };

  cancelConfirm = (): void => {
    this.set({ confirming: false });
    // A Prepare take that never started puts the presenter's pace and persona back.
    const prep = this.prep;
    if (prep && !prep.started) {
      this.prep = null;
      this.choose(prep.personaId);
      this.set({ speedText: prep.speedText, pauseText: prep.pauseText, prepare: null });
    }
  };

  retry = (): void => {
    if (this.view.status !== "error") return;
    if (this.view.errorKind === "start") {
      void this.startTake(this.stepOnce, false);
      return;
    }
    this.set({ status: "running", errorKind: null });
    this.advance();
  };

  private async startTake(step: boolean, counts: boolean): Promise<void> {
    const persona = personaOf(this.view.personaId);
    const inputs = this.inputs;
    if (!persona || !inputs) return;
    if (counts) this.takes += 1;
    this.stepOnce = step;
    this.machine = initialState(persona);
    this.pending = null;
    this.tapRejected = false;
    this.expected = 0;
    this.delivered = 0;
    this.quiet = false;
    this.clearTimer();
    this.clearReplyTimer();
    if (this.prep) this.prep.started = true;
    this.set({
      status: "running",
      pauseReason: null,
      errorKind: null,
      stop: null,
      decision: null,
      confirming: false,
      prepare: this.prep ? { done: 0, total: this.prep.total } : null,
    });
    const { country, locale } = persona.identity;
    // New take already made this phone, in this persona's country and locale: the run keeps it.
    const ready = this.readyPhone !== null && inputs.phone === this.readyPhone ? this.readyPhone : null;
    this.readyPhone = null;
    const phone = ready ?? inputs.newTake(country, locale);
    this.inFlight = true;
    this.takePhone = phone;
    const ok = await inputs.conversation.start({ country, locale, phone, client: "demo", persona_id: persona.id });
    this.inFlight = false;
    if (this.disposed) return;
    if (!ok) this.fail("start");
    // The conversation may have been announced before this returned; a later render finds it too.
    else this.advance();
  }
}

export interface Player {
  view: PlayerView;
  choose: (id: string) => void;
  setPace: (patch: { speedText?: string; pauseText?: string }) => void;
  play: () => void;
  pause: () => void;
  step: () => void;
  confirm: () => void;
  cancelConfirm: () => void;
  retry: () => void;
  /** A fresh phone for the next Play, in the chosen persona's country and locale; null while a run is active. */
  newTake: () => string | null;
  /** Prepare take: the Knockout persona played to its end at the fastest pace. */
  prepare: () => void;
  /** Leaves the run behind and goes idle (reopening a stored Take). */
  release: () => void;
}

/**
 * The playback control's state and commands. It reads the stage through StagePlayerContext only: the
 * conversation is the session's, and the sim probe is the one StageScreen already asks.
 */
export function usePlayer(): Player {
  const stage = useContext(StagePlayerContext);
  const [engine] = useState(() => new PlayerEngine());
  const view = useSyncExternalStore(engine.subscribe, engine.getSnapshot);
  const summary = stage?.session.application.application ?? null;
  const detail = useStageDetail(summary?.id ?? null);

  useEffect(() => {
    if (!stage) return;
    // The detail is live (the stream invalidates it); the lookup's summary is the first answer, then stale.
    const application = detail ?? (summary ? { stage: summary.stage, status: summary.status, outcome: summary.outcome ?? null } : null);
    engine.sync({
      conversation: stage.session.conversation,
      newTake: stage.session.newTake,
      setDraft: stage.setDraft,
      composerEdits: stage.composerEdits,
      probe: stage.probe,
      phone: stage.session.phone,
      application,
    });
  });
  useEffect(() => () => engine.dispose(), [engine]);

  return {
    view,
    choose: engine.choose,
    setPace: engine.setPace,
    play: engine.play,
    pause: engine.pauseNow,
    step: engine.step,
    confirm: engine.confirm,
    cancelConfirm: engine.cancelConfirm,
    retry: engine.retry,
    newTake: engine.newTake,
    prepare: engine.prepare,
    release: engine.release,
  };
}
