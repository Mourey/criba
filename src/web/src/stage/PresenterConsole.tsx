// The Presenter console: the hidden panel on the Demo stage (CONTEXT.md). The host below is always
// mounted on the stage once the gate has passed, and renders nothing until Ctrl+. opens the panel. It
// calls usePlayer() once, so the one engine outlives the panel; it holds the single keydown listener for
// both chords, records each Take when its conversation id appears, and puts the last Take back when the
// stage mounts again. The panel draws that player, the Takes, the Mexico City time against the recording
// window and, on a stack with a sim clock, the clock controls. Nothing links to it from any navigation.

import { useQueries, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useContext, useEffect, useId, useRef, useState } from "react";
import { api } from "../api/client.ts";
import { Button } from "../components/ui/Button.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Field } from "../components/ui/Field.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";
import { stage } from "../lib/labels.ts";
import "../styles/surfaces/stage.css";
import { matchByPhone } from "./phone.ts";
import { PlayerControlsView } from "./PlayerControls.tsx";
import { recordingWindow } from "./recordingWindow.ts";
import { addTake, clearTakes, readTakes, type Take } from "./takes.ts";
import { usePlayer, type Player } from "./usePlayer.ts";
import { StagePlayerContext } from "./useStageSession.ts";

export interface Shortcut {
  key: string;
  /** The modifier is part of the chord: the bare key never matches. */
  ctrl: true;
}

/** Ctrl+. opens and closes the console. */
export const CONSOLE_SHORTCUT: Shortcut = { key: ".", ctrl: true };
/** Ctrl+, pauses, resumes or starts a ready Take, with the console open or closed. */
export const PLAYBACK_SHORTCUT: Shortcut = { key: ",", ctrl: true };

/** Whether a key event is this chord: its key with Ctrl held, and neither Alt nor Meta. */
export function matchesShortcut(event: Pick<KeyboardEvent, "key" | "ctrlKey" | "altKey" | "metaKey">, shortcut: Shortcut): boolean {
  return event.key === shortcut.key && event.ctrlKey === shortcut.ctrl && !event.altKey && !event.metaKey;
}

/** The Take's Application: its outcome, else its status. Looked up by the exact phone, never stored. */
const outcomeKey = ["stage-take-outcome"] as const;

function TakesList({ takes, onReopen, canReopen }: { takes: Take[]; onReopen: (take: Take) => void; canReopen: boolean }) {
  const results = useQueries({
    queries: takes.map((take) => ({
      queryKey: [...outcomeKey, take.phone],
      queryFn: ({ signal }: { signal: AbortSignal }) => api.listApplications({ q: take.phone, limit: 50 }, signal),
      select: (list: Awaited<ReturnType<typeof api.listApplications>>) => matchByPhone(list.items, take.phone),
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    })),
  });
  const failed = results.some((result) => result.isError);

  const columns: TableColumn<{ take: Take; index: number }>[] = [
    { key: "conversation", header: stage.takesConversation, mono: true, render: ({ take }) => <span className="stage-console__id" title={take.conversationId}>{take.conversationId}</span> },
    { key: "phone", header: stage.takesPhone, mono: true, render: ({ take }) => take.phone },
    { key: "persona", header: stage.takesPersona, render: ({ take }) => take.personaId },
    {
      key: "outcome",
      header: stage.takesOutcome,
      render: ({ index }) => {
        const result = results[index];
        if (result?.isLoading) return <Skeleton />;
        const application = result?.data ?? null;
        return application ? (application.outcome ?? application.status) : stage.noOutcome;
      },
    },
    {
      key: "reopen",
      header: "",
      render: ({ take }) => (
        <Button onClick={() => onReopen(take)} disabled={!canReopen}>
          {stage.reopenButton}
        </Button>
      ),
    },
  ];

  return (
    <div className="stage-console__takes">
      {failed ? (
        <ErrorState
          message={stage.outcomesError}
          onRetry={() => {
            for (const result of results) if (result.isError) void result.refetch();
          }}
        />
      ) : null}
      <Table
        caption={stage.takesTitle}
        hideCaption
        columns={columns}
        rows={takes.map((take, index) => ({ take, index }))}
        rowKey={({ take }) => take.conversationId}
        empty={stage.takesEmpty}
      />
    </div>
  );
}

/** The Mexico City time against the recording window: the sim clock's time when it answers, else the browser's. */
function WindowLine({ simNow, loading }: { simNow: Date | null | undefined; loading: boolean }) {
  const [browserNow, setBrowserNow] = useState(() => new Date());
  useEffect(() => {
    if (simNow) return;
    const timer = setInterval(() => setBrowserNow(new Date()), 15_000);
    return () => clearInterval(timer);
  }, [simNow]);
  if (loading) return null;
  const reading = recordingWindow(simNow ?? browserNow);
  return (
    <div className="stage-console__window">
      <span data-testid="console-time" className="stage-console__time">
        {reading.time} {stage.zoneName.MX}
      </span>
      <span>{reading.inside ? stage.windowInside : stage.windowOutside}</span>
    </div>
  );
}

// The reset confirm is the ui Dialog, but a hidden demo console should not make every other page download
// Radix: the Dialog is its own chunk, asked for only when the confirm opens, and gone again when it closes.
// (It splits out of the entry chunk only while the ui barrel file does not re-export it; see the build report.)
const Dialog = lazy(() => import("../components/ui/Dialog.tsx").then((module) => ({ default: module.Dialog })));

const SECONDS_MAX = 7_776_000;

/** Advance and reset for the sim clock. Rendered only when the probe passes. Each call is followed by a refetch of the clock. */
function ClockControls() {
  const queryClient = useQueryClient();
  const [seconds, setSeconds] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // The Dialog has no trigger element (it is mounted only while it is open), so Radix has nowhere to send
  // focus when it closes: it goes back to the Reset button here.
  const resetRow = useRef<HTMLDivElement | null>(null);
  const focusPending = useRef(false);
  useEffect(() => {
    if (confirming) focusPending.current = true;
    else if (focusPending.current && !busy) {
      focusPending.current = false;
      resetRow.current?.querySelector("button")?.focus();
    }
  }, [confirming, busy]);
  const valid = /^\d+$/.test(seconds) && Number(seconds) >= 1 && Number(seconds) <= SECONDS_MAX;

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(false);
    try {
      await action();
      // The clock ticks on its own between fetches, so a jump is invisible until the clock is asked again.
      await queryClient.invalidateQueries({ queryKey: ["sim-clock"] });
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const advance = (by: number) => run(() => api.advanceSimClock({ seconds: by }));

  return (
    <div role="group" aria-label={stage.clockControls} className="stage-console__clock">
      <div className="stage-console__row">
        {stage.advancePresets.map((preset) => (
          <Button key={preset.label} onClick={() => void advance(preset.seconds)} disabled={busy}>
            {preset.label}
          </Button>
        ))}
      </div>
      <div className="stage-console__row">
        <Field
          label={stage.advanceSeconds}
          type="number"
          min={1}
          max={SECONDS_MAX}
          step={1}
          inputMode="numeric"
          value={seconds}
          onChange={(event) => setSeconds(event.target.value)}
          style={{ width: "calc(var(--space-9) * 2)" }} /* geometry */
        />
        <Button onClick={() => void advance(Number(seconds))} disabled={busy || !valid}>
          {stage.advanceButton}
        </Button>
      </div>
      <div className="stage-console__row" ref={resetRow}>
        <Button variant="danger" onClick={() => setConfirming(true)} disabled={busy}>
          {stage.resetButton}
        </Button>
      </div>
      {failed ? (
        <p role="alert" className="ui-field__error">
          {stage.clockChangeError}
        </p>
      ) : null}
      {confirming ? (
        <Suspense fallback={null}>
          <Dialog title={stage.resetTitle} description={stage.resetConfirm} open onOpenChange={setConfirming}>
            <div className="stage-console__row">
              <Button onClick={() => setConfirming(false)}>{stage.cancelLabel}</Button>
              <Button
                variant="danger"
                onClick={() => {
                  setConfirming(false);
                  void run(() => api.resetSimClock());
                }}
              >
                {stage.resetAction}
              </Button>
            </div>
          </Dialog>
        </Suspense>
      ) : null}
    </div>
  );
}

function ConsolePanel({ player, takes, onReopen, onClear }: { player: Player; takes: Take[]; onReopen: (take: Take) => void; onClear: () => void }) {
  const context = useContext(StagePlayerContext);
  const probe = context?.probe;
  const { view } = player;
  const [clearing, setClearing] = useState(false);
  return (
    <>
      <PlayerControlsView player={player} />
      <div role="group" aria-label={stage.takeGroup} className="stage-console__row">
        <Button onClick={() => void player.newTake()} disabled={!view.canNewTake}>
          {stage.newTakeButton}
        </Button>
        <Button onClick={player.prepare} disabled={!view.canPrepare}>
          {stage.prepareTakeButton}
        </Button>
        <Button onClick={() => setClearing(true)} disabled={!view.canNewTake}>
          {stage.clearButton}
        </Button>
        {view.prepare ? (
          <span role="status" className="ui-field__hint">
            {stage.prepareProgress(view.prepare.done, view.prepare.total)}
          </span>
        ) : null}
      </div>
      <TakesList takes={takes} onReopen={onReopen} canReopen={view.canNewTake} />
      <WindowLine simNow={probe?.now} loading={probe?.isLoading ?? false} />
      {probe?.available && probe.now ? <ClockControls /> : null}
      {clearing ? (
        <Suspense fallback={null}>
          <Dialog title={stage.clearTitle} description={stage.clearConfirm} open onOpenChange={setClearing}>
            <div className="stage-console__row">
              <Button onClick={() => setClearing(false)}>{stage.cancelLabel}</Button>
              <Button
                variant="danger"
                disabled={!view.canNewTake}
                onClick={() => {
                  setClearing(false);
                  onClear();
                }}
              >
                {stage.clearAction}
              </Button>
            </div>
          </Dialog>
        </Suspense>
      ) : null}
    </>
  );
}

/**
 * The console host. Ctrl+. opens and closes the panel; Escape inside it closes it, and focus goes back to
 * what had it. Ctrl+, pauses a running Take, resumes a paused one and starts a ready one (a persona is
 * chosen and nothing has run), with the panel open or closed.
 */
export function StageConsole() {
  const player = usePlayer();
  const context = useContext(StagePlayerContext);
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [takes, setTakes] = useState<Take[]>(readTakes);
  const titleId = useId();
  const panel = useRef<HTMLElement | null>(null);
  const openNow = useRef(false);
  const returnTo = useRef<HTMLElement | null>(null);
  // The listener is registered once and reads the latest player through this, never a stale closure.
  const latest = useRef({ player, context });
  useEffect(() => {
    latest.current = { player, context };
  });

  // A Take is recorded when its conversation id appears: from the player, or from the start card by hand.
  const conversationId = context?.session.conversation.conversationId ?? null;
  useEffect(() => {
    const { player: current, context: stageContext } = latest.current;
    if (!stageContext || conversationId === null) return;
    if (readTakes().some((take) => take.conversationId === conversationId)) return;
    const { phone, country, locale } = stageContext.session;
    setTakes(addTake({ conversationId, phone, personaId: current.view.personaId || "manual", country, locale, startedAt: new Date().toISOString() }));
  }, [conversationId]);

  // The stage mounting again (Stage, Recruiter, Stage) puts the last Take back on the phone.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current || !context?.resumeTake) return;
    resumed.current = true;
    const last = readTakes()[0];
    if (last && context.session.conversation.conversationId === null) context.resumeTake(last);
  }, [context]);

  // Outcomes are looked up again whenever a run changes state.
  const status = player.view.status;
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: outcomeKey });
  }, [status, queryClient]);

  const reopen = (take: Take) => {
    const { player: current, context: stageContext } = latest.current;
    current.release();
    stageContext?.resumeTake?.(take);
  };

  // Clear: an empty Takes list and a fresh New take on an empty phone. Client-side only: nothing is sent to
  // the server, and the Applications of past Takes stay in the database and on the dashboards.
  const clear = () => {
    const { player: current } = latest.current;
    if (!current.view.canNewTake) return;
    current.release();
    setTakes(clearTakes());
    current.newTake();
  };

  useEffect(() => {
    const setPanel = (next: boolean) => {
      if (next === openNow.current) return;
      openNow.current = next;
      if (next) returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setOpen(next);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (matchesShortcut(event, CONSOLE_SHORTCUT)) {
        event.preventDefault();
        if (!event.repeat) setPanel(!openNow.current);
      } else if (matchesShortcut(event, PLAYBACK_SHORTCUT)) {
        event.preventDefault();
        if (event.repeat) return;
        const { view, play, pause } = latest.current.player;
        if (view.status === "running") pause();
        else if (view.status === "paused" || (view.status === "idle" && view.personaId !== "")) play();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Opening moves focus into the panel; closing gives it back, unless the presenter has moved on to
  // something else (focus is then not on the removed panel, which leaves it on the body).
  useEffect(() => {
    if (open) {
      panel.current?.focus();
      return;
    }
    const target = returnTo.current;
    returnTo.current = null;
    if (target?.isConnected && (document.activeElement === null || document.activeElement === document.body)) target.focus();
  }, [open]);

  if (!open) return null;
  return (
    <section
      ref={panel}
      className="stage-console__panel"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={(event) => {
        // The reset Dialog is portalled out of the panel but bubbles through it: its Escape is its own.
        if (event.key !== "Escape" || event.defaultPrevented || !panel.current?.contains(event.target as Node)) return;
        event.preventDefault();
        openNow.current = false;
        setOpen(false);
      }}
    >
      <h2 id={titleId} className="stage-console__title">
        {stage.consoleTitle}
      </h2>
      <ConsolePanel player={player} takes={takes} onReopen={reopen} onClear={clear} />
    </section>
  );
}
