// Copy for the Operator's demo stage (/stage). CONTEXT.md term: "Demo stage", never a bare "Stage",
// which is the screening Stage of an Application. English; the agent's own messages stay in the
// language the channel sends.

export const heading = "Demo stage";
export const stubSentence =
  "A live Screening will appear here on the demo stage: the Candidate's chat beside the Application the Screening Agent fills in.";

export const phoneRegion = "Phone";
// The name the channel gives the Client's agent unless it is set otherwise (CRIBA_PERSONA_NAME). It is
// the header until the channel names the agent, so the name does not change when a Take starts.
export const personaFallback = "Sazón Empleo";
export const recordRegion = "Record";
export const clockRegion = "Simulated clock";
export const zoneName = { MX: "Mexico City", ES: "Madrid" } as const;

// Phone: header, log and composer.
export const statusOnline = "online";
export const statusTyping = "typing…";
export const messagesLabel = "Messages";
export const messageLabel = "Message";
export const messagePlaceholder = "Write a message";
export const sendButton = "Send";
export const emptyChat = "Say hello to start. The Candidate writes first.";
export const teamFallback = "Team";

// Phone: start card.
export const startHeading = "New Take";
export const countryField = "Country";
export const phoneField = "Candidate's phone number";
export const startButton = "Start conversation";

// Phone: ticks, reply buttons, list sheet and the .ics card.
export const tickLabel = { sent: "Sent", delivered: "Delivered", read: "Read" } as const;
export const optionsButton = "Options";
export const optionsSheet = "Options";
export const optionsChosen = "Options ·";
export const replyGroup = "Reply options";
export const inviteType = "Interview invite";

// Record: states, views and rows.
export const recordEmpty = "The Application appears here after the Candidate's first message";
export const recordWaiting = "Waiting for the Application";
export const recordWaitingHint = "It appears here as soon as the Screening Agent creates it.";
export const recordLoading = "Loading the Application";
export const lookupError = "Could not look up the Application.";
export const detailError = "Could not load the Application.";
export const viewsLabel = "Record views";
export const agentView = "Agent's view";
export const recruiterView = "Recruiter's view";
export const answersTitle = "Answers";
export const eventsTitle = "Events";
export const eventsList = "Latest events";
export const summaryTitle = "Summary";
export const queueList = "Waiting for a person";
export const flagsLabel = "Flags";
export const claimLabel = "Claim";
export const controlLabel = "Control";
export const locationsLabel = "Locations";
export const zoneLabel = "Zone";
export const interviewLabel = "Interview";
export const unclaimed = "Unclaimed";
export const noInterview = "No Interview yet";
export const pending = "Pending";
export const takeover = "Takeover";
export const stageChangedTo = "→";
export const noValue = "-";
export const ghostValue = "—";

export function waitingSince(kind: string, time: string): string {
  return `Waiting for ${kind.toLowerCase()} since ${time}`;
}

export function earlierEvents(count: number): string {
  return `and ${count} earlier`;
}

export function attempts(count: number): string {
  return `${count} attempts`;
}

// Playback control (the Playback group of the Presenter console).
export const playbackGroup = "Playback";
export const personaLabel = "Persona";
export const personaPlaceholder = "Choose a persona";
export const playLabel = "Play";
export const pauseLabel = "Pause";
export const stepLabel = "Step";
export const retryLabel = "Retry";
export const speedLabel = "Characters per second";
export const pauseSecondsLabel = "Pause before each message (seconds)";
export const costLine = "About $0.01 per conversation";
export const confirmAnother = "Start another take";
export const cancelLabel = "Cancel";
export const confirmQuestion = "This stack uses a live model.";
export const playerIdle = "Choose a persona to play";
export const playerReady = "Ready";
export const playerRunning = "Running";

// Why the player is paused, keyed by the player's pause reason.
export const pauseReasons = {
  user: "Paused",
  composer: "Paused: you typed in the composer",
  hand_send: "Paused: you sent a message",
  hand_tap: "Paused: you tapped a reply",
  no_reply: "Paused: no reply for 60 seconds",
  step: "Paused: one message sent",
  new_take: "Paused: a new take was started",
} as const;

// What went wrong, keyed by the player's error kind.
export const playerErrors = {
  start: "Could not start the take",
  send: "Could not send the message",
} as const;

/** The picker's row for a persona the browser cannot play. */
export function personaUnavailable(id: string, reason: string | null): string {
  return `${id} (unavailable: ${reason ?? "not playable"})`;
}

// Why a run stopped, in words.
export const stopCompleted = "the agent asked nothing more";
export const stopKnockout = "Knockout";
export const stopStopped = "the persona stopped";
export const stopMaxTurns = "60 messages sent";
export function stopNoReply(state: string): string {
  return `the persona has no reply for ${state}`;
}
export function stopTerminal(detail: string | null): string {
  return `the Application reached ${detail ?? "its end"}`;
}

// The last-decision line: what the player detected and which reply it sent.
export const decisionOpening = "sent the opening";
export const decisionTapRejected = "tap not accepted, sent the reply text";
export const decisionConfirmDefault = "detected confirm, sent a plain yes";
export function decisionInterrupt(at: string | null): string {
  return `sent the interrupt at ${at}`;
}
export function decisionConfirm(index: number, count: number): string {
  return `detected confirm, sent confirm reply ${index} of ${count}`;
}
export function decisionCorrection(guessed: boolean, detected: string | null): string {
  return `${guessed ? "guessed" : "detected"} ${detected}, repeated the correction`;
}
export function decisionReply(guessed: boolean, detected: string | null, index: number, count: number): string {
  return `${guessed ? "guessed" : "detected"} ${detected}, sent reply ${index} of ${count}`;
}

// Presenter console.
export const consoleTitle = "Presenter console";
export const windowInside = "Inside the recording window";
export const windowOutside = "Outside the recording window (09:00 to 22:00)";
export const takesEmpty = "No takes yet. New take starts one.";
export const resetConfirm = "Reset moves the clock back to real time. Advances made so far are lost.";

// Presenter console: Take, takes list, recording window and clock.
export const newTakeButton = "New take";
export const prepareTakeButton = "Prepare take";
export function prepareProgress(done: number, total: number): string {
  return `Preparing: ${done} of ${total}`;
}
export const takeGroup = "Take";
export const takesTitle = "Takes";
export const takesConversation = "Conversation";
export const takesPhone = "Phone";
export const takesPersona = "Persona";
export const takesOutcome = "Outcome";
export const reopenButton = "Reopen";
export const outcomesError = "Could not look up the outcomes.";
export const noOutcome = "-";
export const clockControls = "Clock controls";
export const advanceSeconds = "Advance by (seconds)";
export const advanceButton = "Advance";
export const advancePresets = [
  { label: "Advance 15 minutes", seconds: 900 },
  { label: "Advance 1 hour", seconds: 3600 },
  { label: "Advance 1 day", seconds: 86400 },
] as const;
export const resetButton = "Reset clock";
export const resetTitle = "Reset the clock?";
export const resetAction = "Reset to real time";
export const clockChangeError = "Could not change the clock.";

// Record: the "View as" switch, Approve and the panel under the record (ADR 0075). The switch is a
// presentation choice of this screen only: the server still sees the Operator.
export const viewAsLabel = "View as";
export const viewAsOperator = "Operator";
export const viewAsRecruiter = "Recruiter";
export const approveButton = "Approve";
export const approvedStatus = "Approved. The interview times go to the Candidate the usual way.";
export const approveFailed = (message: string) => `Could not approve. ${message}`;
export const underRecordRegion = "Conversation readings";
export const underRecordTabs = "Readings";
// The closed panel's one-line strip (Operator view); in Recruiter view it reads messageLabelsTitle.
export const underRecordToggleOperator = "Agent activity and Jev readings";
export const jevReadingsTab = "Jev readings";
export const messageLabelsTitle = "Message labels";

// Presenter console: Clear. Client-side only: the server keeps every Application.
export const clearButton = "Clear";
export const clearTitle = "Clear the stage?";
export const clearConfirm = "Past takes stay on the dashboards. This empties the phone and the Takes list and starts a fresh New take.";
export const clearAction = "Clear the stage";

// Agent activity (ADR 0075): what happened on each Candidate turn, from stored records only. Operator view.
export const activityTab = "Agent activity";
export const activityLegend =
  "Candidate → our backend (channel and router) → Jev reads the message → Screening Agent on Hermes, model via OpenRouter → tools proxied to our backend (/internal/tools) → code validates and records → reply.";
export const activityList = "Turns";
export const activityEmpty = "Nothing has happened on this Application yet.";
export const activityBefore = "Before the Candidate's first message";
export const activityUnavailable = "Could not load the agent's activity.";
export const activityRetry = "Retry";
export const activityIn = "Candidate message in";
export const activityKind = { typed: "typed", tap: "tap", spoken: "spoken", media: "media" } as const;
export const activityJev = "Jev read";
export const activityJevNotRead = "Jev did not read it";
export const activityJevStatus = {
  error: "Jev failed (error)",
  timeout: "Jev timed out",
  expired: "Jev's reading was deleted after the retention period",
  not_read: "No Jev reading",
} as const;
export const activityRun = "Agent run";
export const activityTokens = (input: string, output: string) => `${input} in / ${output} out`;
export const activityOutcome = {
  ok: "accepted by code",
  invalid: "rejected by code",
  knockout: "Knockout decided by code",
  error: "failed",
} as const;
export const activityCode = "Code";
export const activityReply = "Reply out";
export const activityButtons = (count: number) => `with ${count} reply ${count === 1 ? "option" : "options"}`;
export const activityNoRuns = "No agent run recorded on this turn.";
