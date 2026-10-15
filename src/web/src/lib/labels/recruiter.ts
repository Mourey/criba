// Copy for the Recruiter's pages (Applications, Application detail, Review queue, Slots). Terms follow
// CONTEXT.md. Phase 4 adds names here without editing labels.ts.

import type { ApplicationFilters } from "../../api/types.ts";

export const surface = "Recruiter";

// Applications.
export const applicationsTitle = "Applications";
export const applicationsFilterForm = "Filter Applications";
export const applicationsTableRegion = "Applications table";
export const filterSearch = "Search Candidate";
export const filterSearchPlaceholder = "Name or phone";
export const filterLocation = "Location";
export const filterAllLocations = "All Locations";
export const filterCountry = "Country";
export const filterBothCountries = "Both";
export const filterStatus = "Status";
export const filterStatusAny = "Open and closed";
export const filterStatusOpen = "Open";
export const filterStatusClosed = "Closed";
export const filterClaim = "Claim";
export const filterClaimAny = "Any";
export const filterClaimMine = "Claimed by me";
export const filterClaimNone = "Unclaimed";
export const filterCreatedFrom = "Created from";
export const filterCreatedTo = "Created to";
export const filterSort = "Sort";
export const filterStage = "Stage";
export const filterOutcome = "Outcome";
export const filterFlags = "Flags";
export const filterClear = "Clear filters";
export const filterMore = "More filters";
/** Read after "More filters" when hidden filters are set: the badge shows the number, this says what it is. */
export const filterMoreActive = (count: number) => `${count} active`;
export const applicationsLoadMore = "Load more";
export const applicationsEmpty = "No Applications match these filters.";
export const applicationsEmptyNone = "Applications appear here once a Candidate starts Screening.";
export const applicationsEmptyFiltered = "Clear a filter to see more.";
export const applicationsClaimedIdle = "Claimed, idle";
export const applicationsUnclaimed = "Unclaimed";
export const applicationsNoLocation = "-";

export function applicationsFound(total: number): string {
  return `${total} found`;
}

export function applicationsTakeover(name: string | null | undefined): string {
  return `Takeover: ${name}`;
}

export function applicationsLoadFailed(message: string): string {
  return `Could not load Applications. ${message}`;
}

export const SORTS: { value: NonNullable<ApplicationFilters["sort"]>; label: string }[] = [
  { value: "-updated_at", label: "Recently updated" },
  { value: "updated_at", label: "Least recently updated" },
  { value: "-created_at", label: "Newest" },
  { value: "created_at", label: "Oldest" },
  { value: "-time_in_stage", label: "Longest in stage" },
];

export const applicationsColumns = {
  candidate: "Candidate",
  locations: "Locations",
  stage: "Stage",
  claim: "Claim holder",
  inStage: "In stage",
  total: "Total",
  steps: "Time per step",
  flags: "Flags",
  updated: "Updated",
} as const;

// Takeover panel (ADR 0020) and Transcript.
export const takeoverTitle = "Takeover";
export const takeoverAgentHolds =
  "The Screening Agent holds this chat. Starting a Takeover pauses it and tells the Candidate a team member has joined.";
export const takeoverClosed = "This Application is closed.";
export const takeoverStart = "Start Takeover";
export const takeoverMine = "You hold this chat. The Screening Agent is paused and messages you send go to the Candidate.";
export const takeoverOtherHolder = "Another team member";
export const takeoverMessageLabel = "Message to the Candidate";
export const takeoverMessagePlaceholder = "Write to the Candidate";
export const takeoverSend = "Send";
export const takeoverSummaryLabel = "Summary for the Screening Agent (optional; generated from the transcript when empty)";
export const takeoverHandBack = "Hand back to the Screening Agent";
export const takeoverFailed = "Something went wrong.";
// WhatsApp allows only approved templates 24 hours after the Candidate's last message (409).
export const takeoverOutsideWindow =
  "Not sent: the Candidate last wrote more than 24 hours ago, and after that WhatsApp allows only approved templates. Your text is kept; send it once the Candidate writes again.";

export function takeoverHeldBy(name: string | null, canForceHandBack: boolean): string {
  return `${name ?? takeoverOtherHolder} holds this chat. Only they can reply${
    canForceHandBack ? "; as an Operator you can also hand it back." : " or hand it back."
  }`;
}

export const transcriptTitle = "Transcript";
export const transcriptEmpty = "No messages yet.";
export const transcriptOptionsOffered = "Options offered";
export const transcriptTapped = "Tapped option";

// Message labels (ADR 0075): Jev's reading of a typed message as words, for every role. Labels only:
// no score, confidence or threshold, and nothing that judges the person. Each label is plain text.
export const labelsFixedLine = "Communication context from the conversation, not an assessment of the Candidate.";
export const labelsStripName = "How Jev read this message";
export const labelsLanguage = { es: "Spanish", en: "English", other: "Another language", unclear: "Language unclear" } as const;
export const labelsSignal = { frustrated: "Frustrated", confused: "Confused", wants_human: "Wants a person" } as const;
export const labelsIntent = {
  answering: "Answering",
  asking: "Asking a question",
  off_topic: "Off topic",
  wants_to_stop: "Wants to stop",
  other: "Other",
} as const;
export const labelsIntentLine = (word: string) => `Intent: ${word}`;
export const labelsHint = {
  apologise: "apologise",
  simpler: "simpler words",
  offer_person: "offer a person",
  escalated: "team alerted",
  calm: "stay calm",
} as const;
export const labelsHintLine = (word: string) => `Tone hint: ${word}`;
export const labelsLanguageSet = { "es-ES": "Spanish", "es-MX": "Spanish", en: "English" } as const;
export const labelsAskedFor = (language: string) => `Asked for ${language}`;
export const labelsSwitchedTo = (language: string) => `Switched to ${language}`;
export const labelsListTitle = "Message labels";
export const labelsNone = "Jev has read no typed message on this Application yet.";

// Review queue.
export type QueueTab = "approval" | "review" | "escalation" | "rejection";

export const TABS: { id: QueueTab; label: string }[] = [
  { id: "approval", label: "Approvals" },
  { id: "review", label: "Reviews" },
  { id: "escalation", label: "Escalations" },
  { id: "rejection", label: "Rejections" },
];

export const queueTitle = "Review queue";
export const queueTabsLabel = "Queue kinds";
export const queueShow = "Show";
export const queueShowOpen = "Open items";
export const queueShowResolved = "Resolved items";
export const queueRejectionsTable = "Rejections table";
export const queueRejectionsIntro =
  "A Rejection ends an Application at any stage, with a reason. Reject from an Approval below, or from the Application itself.";
export const queueRejectionsEmpty = "No Rejections yet.";
export const queueApprove = "Approve";
export const queueReject = "Reject...";
export const queueUpheld = "Upheld";
export const queueOverturned = "Overturned";
export const queueStartTakeover = "Start Takeover";
export const queueDismiss = "Dismiss";
export const queueNote = "Note (optional)";
export const queueApprovalLine = "Qualified and waiting for Approval.";
export const queueFailed = "Something went wrong.";
export const queueResolvedFallback = "Resolved";

export const rejectionColumns = {
  candidate: "Candidate",
  stage: "Stage when rejected",
  reason: "Reason",
  rejected: "Rejected",
  total: "Total time",
} as const;

export function queueLoadFailed(message: string): string {
  return `Could not load the queue. ${message}`;
}

export function rejectionsLoadFailed(message: string): string {
  return `Could not load Rejections. ${message}`;
}

/** What an empty tab says, and what lands there. */
export const queueEmpty: Record<Exclude<QueueTab, "rejection">, { title: string; hint: string }> = {
  approval: { title: "No Approvals waiting.", hint: "Qualified Applications wait here for your Approval." },
  review: { title: "No Reviews waiting.", hint: "A Candidate who asks for a Review of a Knockout lands here." },
  escalation: { title: "No Escalations waiting.", hint: "Questions the Screening Agent could not answer land here." },
};

export const queueEmptyResolved: Record<Exclude<QueueTab, "rejection">, { title: string; hint: string }> = {
  approval: { title: "No resolved Approvals yet.", hint: "Approvals that someone has decided are listed here." },
  review: { title: "No resolved Reviews yet.", hint: "Reviews that someone has decided are listed here." },
  escalation: { title: "No resolved Escalations yet.", hint: "Escalations that someone has handled are listed here." },
};

// Slots.
export const slotsTitle = "Interview slots";
export const slotsLocation = "Location";
export const slotsPublishTitle = "Publish Interview slots";
export const slotsTimeZoneNote = "Times are in the Location's time zone";
export const slotsDate = "Date";
export const slotsStarts = "Starts";
export const slotsEnds = "Ends";
export const slotsAddToBatch = "Add to batch";
export const slotsBatchList = "Slots to publish";
export const slotsRemove = "Remove";
export const slotsMove = "Move";
export const slotsCancelSlot = "Cancel slot";
export const slotsSave = "Save";
export const slotsCancel = "Cancel";
export const slotsNewDate = "New date";
export const slotsNewStart = "New start time";
export const slotsNewEnd = "New end time";
export const slotsEmpty = "No slots published yet.";
export const slotsEmptyHint = "Publish slots above and they appear here, grouped by day.";
export const slotsNoLocations = "No Locations are available to you yet.";
export const slotsNoLocationsHint = "Slots are published per Location. Ask an Operator to assign you one.";
export const slotsEndBeforeStart = "A slot must end after it starts.";
export const slotsChooseDate = "Choose a date.";
export const slotsFailed = "Something went wrong.";
export const slotsCandidateFallback = "Candidate";

export const statusLabel = {
  open: "Open",
  booked: "Booked",
  cancelled: "Cancelled",
  past: "Past",
} as const;

export function slotsHeading(locationName: string | undefined): string {
  return `Slots at ${locationName ?? "..."}`;
}

export function slotsPublishCount(count: number): string {
  return `Publish ${count} ${count === 1 ? "slot" : "slots"}`;
}

export function slotsBookedVia(via: string): string {
  return via === "chat" ? "booked in chat" : "booked on the booking page";
}

export function slotsLocationsFailed(message: string): string {
  return `Could not load Locations. ${message}`;
}

export function slotsLoadFailed(message: string): string {
  return `Could not load slots. ${message}`;
}

// Results.
export const resultsTitle = "Results";
export const resultsFilterForm = "Filter results";
export const resultsFrom = "From";
export const resultsTo = "To";
export const resultsLocation = "Location";
export const resultsAllLocations = "All Locations";
export const resultsCountry = "Country";
export const resultsBothCountries = "Both";
export const resultsByLocation = "By Location";
export const resultsEmpty = "No Applications in this range.";
export const resultsEmptyHint = "Change the dates, the Location or the country to see more.";
export const resultsColumns = {
  location: "Location",
  applications: "Applications",
  qualified: "Qualified",
  knockouts: "Knockouts",
} as const;

export function resultsRange(from: string, to: string): string {
  return `${from} to ${to}`;
}

export function resultsLoadFailed(message: string): string {
  return `Could not load results. ${message}`;
}
