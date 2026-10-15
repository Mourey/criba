import { useId } from "react";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import "../components/ui/ui.css";
import "../styles/surfaces/stage.css";
import { PERSONAS } from "./playback.ts";
import { describeDecision, errorKindText, pauseReasonText, playerCopy, stopText, unavailableOption } from "./playerCopy.ts";
import { usePlayer, type Player, type PlayerView } from "./usePlayer.ts";

function statusLine(view: PlayerView): string {
  switch (view.status) {
    case "idle":
      return view.personaId ? playerCopy.ready : playerCopy.idle;
    case "running":
      return playerCopy.running;
    case "paused":
      return pauseReasonText(view.pauseReason ?? "user");
    case "stopped":
      return view.stop ? `Stopped: ${stopText(view.stop)}` : "Stopped";
    case "error":
      return errorKindText(view.errorKind ?? "send");
  }
}

/**
 * The playback control for the Demo stage: a persona picker, Play, Pause, Step, the two pace settings,
 * the state and the player's last decision, in one compact row. This view draws a player it is handed,
 * so the Presenter console can own the one engine and keep it running while the panel is closed.
 */
export function PlayerControlsView({ player }: { player: Player }) {
  const { view, choose, setPace, play, pause, step, confirm, cancelConfirm, retry } = player;
  const ids = { persona: useId(), speed: useId(), pause: useId() };
  const decision = view.decision ? describeDecision(view.decision) : "";

  return (
    <div
      role="group"
      aria-label={playerCopy.groupLabel}
      style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2) var(--space-3)", padding: "var(--space-2) 0" }} /* geometry */
    >
      <label htmlFor={ids.persona} className="ui-field__label">
        {playerCopy.personaLabel}
      </label>
      <select
        id={ids.persona}
        className="ui-field__input"
        value={view.personaId}
        disabled={!view.canChoose}
        onChange={(event) => choose(event.target.value)}
        style={{ width: "auto", maxWidth: "100%" }} /* geometry */
      >
        <option value="">{playerCopy.personaPlaceholder}</option>
        {PERSONAS.map((p) => (
          <option key={p.id} value={p.id} disabled={!p.playable}>
            {p.playable ? p.id : unavailableOption(p.id, p.unplayable_reason)}
          </option>
        ))}
      </select>
      <Button variant="primary" onClick={play} disabled={!view.canPlay}>
        {playerCopy.play}
      </Button>
      <Button onClick={pause} disabled={!view.canPause}>
        {playerCopy.pause}
      </Button>
      <Button onClick={step} disabled={!view.canStep}>
        {playerCopy.step}
      </Button>
      <label htmlFor={ids.speed} className="ui-field__label">
        {playerCopy.speedLabel}
      </label>
      <input
        id={ids.speed}
        type="number"
        min={0}
        step={1}
        inputMode="decimal"
        className="ui-field__input"
        value={view.speedText}
        onChange={(event) => setPace({ speedText: event.target.value })}
        style={{ width: "var(--space-9)" }} /* geometry */
      />
      <label htmlFor={ids.pause} className="ui-field__label">
        {playerCopy.pauseLabel}
      </label>
      <input
        id={ids.pause}
        type="number"
        min={0}
        step={0.1}
        inputMode="decimal"
        className="ui-field__input"
        value={view.pauseText}
        onChange={(event) => setPace({ pauseText: event.target.value })}
        style={{ width: "var(--space-9)" }} /* geometry */
      />
      <span aria-live="polite" data-testid="player-status" className="ui-field__label">
        {statusLine(view)}
      </span>
      {view.status === "error" ? <Button onClick={retry}>{playerCopy.retry}</Button> : null}
      <span
        data-testid="player-decision"
        title={decision}
        className="ui-field__hint"
        style={{ flex: "1 1 var(--space-9)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} /* geometry */
      >
        {decision}
      </span>
      {view.showCost ? <span className="ui-field__hint">{playerCopy.costLine}</span> : null}
      {view.confirming ? (
        <span role="group" aria-label={playerCopy.confirmQuestion} style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }} /* geometry */>
          <span>{playerCopy.confirmQuestion}</span>
          <Button variant="primary" onClick={confirm}>
            {playerCopy.confirmAnother}
          </Button>
          <Button onClick={cancelConfirm}>{playerCopy.cancel}</Button>
        </span>
      ) : null}
    </div>
  );
}

/** The control with an engine of its own: the control as phase 5 shipped it. The console does not use this. */
export function PlayerControls() {
  return <PlayerControlsView player={usePlayer()} />;
}
