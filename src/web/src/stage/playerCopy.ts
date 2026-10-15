// The words of the playback control live in lib/labels/stage.ts. This module keeps the names the
// control and its tests import, and the two functions that choose a phrase for a player value.
// CONTEXT.md's avoided terms apply (check_web_tokens.sh reads this file).

import * as labels from "../lib/labels/stage.ts";
import type { Decision, Stop } from "./playback.ts";

/** Why the player is paused. */
export type PauseReason = keyof typeof labels.pauseReasons;
/** What went wrong. */
export type PlayerErrorKind = keyof typeof labels.playerErrors;

export const playerCopy = {
  groupLabel: labels.playbackGroup,
  personaLabel: labels.personaLabel,
  personaPlaceholder: labels.personaPlaceholder,
  play: labels.playLabel,
  pause: labels.pauseLabel,
  step: labels.stepLabel,
  retry: labels.retryLabel,
  speedLabel: labels.speedLabel,
  pauseLabel: labels.pauseSecondsLabel,
  costLine: labels.costLine,
  confirmAnother: labels.confirmAnother,
  cancel: labels.cancelLabel,
  confirmQuestion: labels.confirmQuestion,
  idle: labels.playerIdle,
  ready: labels.playerReady,
  running: labels.playerRunning,
} as const;

export const pauseReasonText = (reason: PauseReason): string => labels.pauseReasons[reason];
export const errorKindText = (kind: PlayerErrorKind): string => labels.playerErrors[kind];

/** The picker's row for a persona the browser cannot play. */
export const unavailableOption = labels.personaUnavailable;

/** Why a run stopped, in words. */
export function stopText(stop: Stop): string {
  if (stop.reason.startsWith("no_scripted_reply:")) return labels.stopNoReply(stop.reason.slice("no_scripted_reply:".length));
  switch (stop.reason) {
    case "completed":
      return labels.stopCompleted;
    case "knockout":
      return labels.stopKnockout;
    case "stopped":
      return labels.stopStopped;
    case "max_turns":
      return labels.stopMaxTurns;
    case "terminal":
      return labels.stopTerminal(stop.detail ?? null);
    default:
      return stop.reason;
  }
}

/** The last-decision line: what the player detected and which reply it sent. */
export function describeDecision(decision: Decision): string {
  switch (decision.source) {
    case "opening":
      return labels.decisionOpening;
    case "interrupt":
      return labels.decisionInterrupt(decision.at);
    case "tap_rejected":
      return labels.decisionTapRejected;
    case "confirm_default":
      return labels.decisionConfirmDefault;
    case "confirm":
      return labels.decisionConfirm(decision.index, decision.count);
    case "correction":
      return labels.decisionCorrection(decision.guessed, decision.detected);
    default:
      return labels.decisionReply(decision.guessed, decision.detected, decision.index, decision.count);
  }
}
