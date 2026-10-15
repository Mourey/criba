// Copy for the Operator's pages (/overview). Terms follow CONTEXT.md; no cost figures on Overview.
// The tab labels and the figures of the economics views live with those views (dashboards/Economics.tsx).
import type { Stage } from "../../api/types.ts";

export const heading = "Overview";
export const stubSentence =
  "How Screening is going across every Location will appear here: Applications by stage, Outcomes and open Escalations.";

/**
 * The pipeline: the open Applications by their current stage, in five ordered groups. `greeting` is the
 * Application just started ("Applied"); the nine screening stages up to `confirm` are Screening. This is a
 * count of where Applications are now, not a cumulative funnel, so no rate is derived from it.
 */
export interface PipelineGroup {
  id: string;
  label: string;
  stages: readonly Stage[];
}

export const PIPELINE_GROUPS: readonly PipelineGroup[] = [
  { id: "applied", label: "Applied", stages: ["greeting"] },
  {
    id: "screening",
    label: "Screening",
    stages: ["name", "zone", "driver_license", "availability", "preferred_schedule", "experience", "start_date", "confirm"],
  },
  { id: "qualified", label: "Qualified", stages: ["qualified"] },
  { id: "approved", label: "Approved", stages: ["approved"] },
  { id: "interview_booked", label: "Interview booked", stages: ["interview_booked"] },
];

export const pipelineTitle = "Pipeline";
export const pipelineCaption = "Open Applications by their current stage. Each Application is counted once.";
export const pipelineLabelHeader = "Stage";
export const outcomesTitle = "Applications by Outcome";
export const outcomesCaption = "Closed Applications, by how they ended.";
export const outcomesLabelHeader = "Outcome";

export const pipelineEmpty = "No open Applications right now.";
export const outcomesEmpty = "No Applications have closed in this period.";

export const signalsTitle = "Signals";
export const signalEscalations = "Escalations";
export const signalTakeovers = "Takeovers";
export const signalQualifiedWaiting = "Qualified, waiting";
export const signalQualifiedWaitingHint = "Unclaimed for 14 days without an Approval";

export const byLocationTitle = "By Location";
export const byLocationColumns = { location: "Location", applications: "Applications", qualified: "Qualified", knockouts: "Knockouts" };

export const tabsLabel = "More figures";
export const loadingPipeline = "Loading the pipeline";

export const empty = "No Applications in this period. The pipeline fills as Candidates start chatting.";
export const loadFailed = (message: string) => `Could not load the Overview. ${message}`;
export const period = (from: string, to: string) => `${from} to ${to}`;

/** The status line after a failed refresh: the figures on screen are the last good ones. */
export function refreshFailed(updatedAt: number): string {
  const time = new Date(updatedAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `Could not refresh. Showing data from ${time}.`;
}

// The Operator panel on the Application page (components/OperatorPanel.tsx). It repeats no message text
// (ADR 0035): only Jev's flags per typed message and the Application's agent run errors. The block of
// figures keeps its heading and labels beside it in the component, as on the page before.
export const panelTitle = "Operator panel";
export const panelFixedLine = "Jev's reading of each typed message. Not an assessment of the Candidate.";
export const panelSignalsTitle = "Signals";
export const panelCountsLabel = "Signal counts";
export const panelRowsLabel = "Messages with a signal";
export const panelSignalsAbsent = "This server does not return signals.";
export const panelSignalsNone = "No signal fired on this Application.";
export const panelLanguageChanged = "Language changed";
export const panelEscalated = "Escalation raised";
export const panelShowInTranscript = "Show in Transcript";
export const panelRunErrorsTitle = "Agent run errors";
export const panelRunErrorsNone = "No agent run errors on this Application.";
export const panelSystemTurn = "System turn";
export const panelCandidateTurn = "Not a system turn";
export const panelRun = (runId: string) => `Run ${runId}`;

/** Jev's four signals, in the order the summary strip counts them. Text labels, never colour alone. */
export const SIGNALS = ["frustrated", "confused", "wants_human", "abusive"] as const;
export type Signal = (typeof SIGNALS)[number];
export const signalLabel: Record<Signal, string> = {
  frustrated: "Frustrated",
  confused: "Confused",
  wants_human: "Wants a person",
  abusive: "Abusive",
};

export const runErrorKindLabel: Record<string, string> = { error: "Error", timeout: "Timeout" };

// Jev's full reading of each typed message, for Operators (jev-readings, ADR 0066). The four non-ok lines
// are the spec's copy, verbatim. Scores and thresholds are numbers: no message text appears here.
export const readingsListLabel = "Jev readings by message";
export const readingsPeaksLabel = "Peak score per signal";
export const readingsLegend = "Ticks mark thresholds. Wants a person: short = hint, tall = Escalation.";
export const panelCountColumn = "Count";
export const readingsPeakColumn = "Peak";
export const panelNotInTranscript = "This message is not in the loaded Transcript.";
export const readingsUnavailable = "Scores unavailable";
export const readingsRetry = "Retry";
export const readingStatusLine: Record<"error" | "timeout" | "expired" | "not_read", string> = {
  error: "Jev could not read this message (error)",
  timeout: "Jev could not read this message (timed out)",
  expired: "Jev's reading was deleted after the retention period.",
  not_read: "Jev did not read this message (Jev off, a Takeover, an opt-out or a failure).",
};
export const thresholdsUnavailable = (version: string | null) => `Thresholds of policy ${version ?? "unknown"} are not available.`;

/** The five bars of an ok reading: the language switch score, then the four tones (the strip's SIGNALS). */
export const BARS = ["switch", "frustrated", "confused", "wants_human", "abusive"] as const;
export type Bar = (typeof BARS)[number];
export const barLabel: Record<Bar, string> = { switch: "Language switch", ...signalLabel };

/** A marker's name on the wants-human bar, which has two; the other bars have one unnamed marker. */
export const markerHint = "hint";
export const markerEscalation = "Escalation";
export const markerText = (name: string | null, value: string) => `${name ? `${name} ` : ""}threshold ${value}`;
export const barText = (bar: Bar, score: string, markers: readonly string[]) => [`${barLabel[bar]} ${score}`, ...markers].join(", ");

/** Jev's intent reading of a typed message, in the contract's order. Plain text, never a Tag and never a colour (display only, ADR 0051). */
export const INTENTS = ["answering", "asking", "off_topic", "wants_to_stop", "steering", "other"] as const;
export type Intent = (typeof INTENTS)[number];
export const intentLabel: Record<Intent, string> = {
  answering: "Answering",
  asking: "Asking a question",
  off_topic: "Off topic",
  wants_to_stop: "Wants to stop",
  steering: "Steering the agent",
  other: "Other",
};
export const intentLine = (label: string, confidence: string) => `Intent: ${label} (${confidence})`;
/** The sixth bar: Jev's Manipulation score. It has no threshold, so it draws no marker and never reads as fired. */
export const manipulationLabel = "Manipulation";
export const manipulationText = (score: string) => `${manipulationLabel} ${score}`;
export const intentNone = "No intent or manipulation reading.";

export const languageName = { es: "Spanish", en: "English", other: "Another language", unclear: "Unclear" } as const;
export const languageText = (lang: keyof typeof languageName, confidence: string, threshold: string | null) =>
  threshold === null ? `${languageName[lang]} ${confidence}` : `${languageName[lang]} ${confidence}, threshold ${threshold}`;
export const hintLine = (code: string) => `Tone hint: ${code}`;
export const modelLine = (model: string | null, policyVersion: string | null) => `${model ?? "-"} · policy ${policyVersion ?? "-"}`;

// The agent trace (agent-trace-web, ADR 0069): the Application's agent runs and the tool calls of each. Names, codes and
// numbers only, never a message, a reasoning text or a tool result. The cost label is in OperatorPanel.tsx, as the Cost card's is.
export const traceTitle = "Agent runs";
export const traceRunsLabel = "Runs with their tool calls";
export const traceCallsLabel = "Tool calls";
export const traceRunNotRecorded = "Run not recorded";
export const traceNone = "No tool calls recorded on this Application.";
export const traceShowReply = "Show reply in Transcript";
export const traceNoReply = "This run has no reply message.";
export const traceNotStored = "(not stored)";
export const traceUnavailable = "Trace unavailable";
export const traceRetry = "Retry";
export const traceRetryLabel = "Retry the agent runs";
export const traceTokensIn = "Tokens in";
export const traceTokensOut = "Tokens out";
export const traceLatency = "Latency";
export const traceOutcomeLabel: Record<"ok" | "invalid" | "knockout" | "error", string> = {
  ok: "OK",
  invalid: "Invalid",
  knockout: "Knockout",
  error: "Error",
};
export const traceStatusLabel: Record<"ok" | "error" | "timeout", string> = { ok: "OK", error: "Error", timeout: "Timeout" };
