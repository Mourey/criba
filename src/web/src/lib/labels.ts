// UI labels. Every term follows CONTEXT.md (Application, Claim, Claimed idle, Knockout vs Rejection,
// Stalled vs Abandoned, Takeover, Operator, ...). Do not use the _Avoid_ synonyms.
import type {
  AppEvent,
  EscalationReason,
  Flag,
  Outcome,
  QueueKind,
  RejectionReason,
  Stage,
} from "../api/types.ts";

// Per-surface copy. Each surface owns its module and adds names there, never here.
export * as today from "./labels/today.ts";
export * as recruiter from "./labels/recruiter.ts";
export * as operator from "./labels/operator.ts";
export * as stage from "./labels/stage.ts";
export * as booking from "./labels/booking.ts";

/** The Client this deployment screens for (CONTEXT.md: Client). */
export const CLIENT_NAME = "Grupo Sazón";

export const STAGES: readonly Stage[] = [
  "greeting",
  "name",
  "zone",
  "driver_license",
  "availability",
  "preferred_schedule",
  "experience",
  "start_date",
  "confirm",
  "qualified",
  "approved",
  "interview_booked",
];

export const SCREENING_STAGES: readonly Stage[] = STAGES.slice(0, 9);

export const stageLabel: Record<Stage, string> = {
  greeting: "Greeting",
  name: "Name",
  zone: "Zone",
  driver_license: "Driver's license",
  availability: "Availability",
  preferred_schedule: "Preferred schedule",
  experience: "Experience",
  start_date: "Start date",
  confirm: "Confirm",
  qualified: "Qualified",
  approved: "Approved",
  interview_booked: "Interview booked",
};

export const OUTCOMES: readonly Outcome[] = ["knockout", "rejection", "abandoned", "withdrawn"];

export const outcomeLabel: Record<Outcome, string> = {
  knockout: "Knockout",
  rejection: "Rejection",
  abandoned: "Abandoned",
  withdrawn: "Withdrawn",
};

export const FLAGS: readonly Flag[] = [
  "review_requested",
  "escalation",
  "stalled",
  "takeover",
  "qualified_waiting",
  "claimed_idle",
  "start_date_far",
  "opted_out",
];

export const flagLabel: Record<Flag, string> = {
  review_requested: "Review",
  escalation: "Escalation",
  stalled: "Stalled",
  takeover: "Takeover",
  qualified_waiting: "Qualified, waiting",
  claimed_idle: "Claimed, idle",
  start_date_far: "Start date far",
  opted_out: "Opted out",
};

/** Flags that need a person's attention get the warm tone; the rest are informational. */
export const flagNeedsAttention: Record<Flag, boolean> = {
  review_requested: true,
  escalation: true,
  stalled: false,
  takeover: false,
  qualified_waiting: true,
  claimed_idle: true,
  start_date_far: false,
  opted_out: false,
};

export const REJECTION_REASONS: readonly RejectionReason[] = ["no_show", "failed_call", "role_filled", "other"];

export const rejectionReasonLabel: Record<RejectionReason, string> = {
  no_show: "No-show",
  failed_call: "Failed call",
  role_filled: "Role filled",
  other: "Other (note required)",
};

export const queueKindLabel: Record<QueueKind, string> = {
  approval: "Approval",
  review: "Review",
  escalation: "Escalation",
};

export const escalationReasonLabel: Record<EscalationReason, string> = {
  repeated_invalid_answers: "Repeated invalid answers",
  abuse: "Abuse",
  unanswered_question: "Question the Screening Agent could not answer",
  wants_human: "Candidate asked for a person",
  frustrated: "Candidate is frustrated",
  technical_error: "Technical error",
  other: "Other",
  unreadable: "Unreadable messages: the agent has stopped replying",
};

export const resolutionLabel: Record<string, string> = {
  approved: "Approved",
  rejected: "Rejection",
  upheld: "Upheld",
  overturned: "Overturned",
  dismissed: "Dismissed",
  takeover: "Takeover started",
};

export const answerFieldLabel: Record<string, string> = {
  full_name: "Full name",
  zone: "Zone",
  driver_license: "Driver's license",
  availability: "Availability",
  preferred_schedule: "Preferred schedule",
  experience_years: "Years of experience",
  experience_platforms: "Platforms",
  start_date: "Start date",
};

export const answerStatusLabel: Record<string, string> = {
  valid: "Valid",
  pending: "Pending",
  invalid: "Invalid",
  flagged: "Flagged",
  knockout: "Knockout",
};

export const eventLabel: Record<AppEvent["kind"], string> = {
  application_started: "Application started",
  stage_changed: "Stage changed",
  reopened: "Reopened",
  answer_recorded: "Answer recorded",
  answer_invalid: "Answer invalid",
  answer_ambiguous: "Answer ambiguous",
  screening_finished: "Screening finished",
  summary_written: "Summary written",
  knockout: "Knockout",
  rejection: "Rejection",
  abandoned: "Abandoned",
  withdrawn: "Withdrawn",
  review_requested: "Review requested",
  review_upheld: "Review upheld",
  review_overturned: "Review overturned",
  escalation_raised: "Escalation raised",
  escalation_dismissed: "Escalation dismissed",
  approval_requested: "Approval requested",
  approved: "Approved",
  claim_taken: "Claim taken",
  claim_released: "Claim released",
  claim_taken_over: "Claim taken over",
  takeover_started: "Takeover started",
  takeover_handed_back: "Takeover handed back",
  stalled: "Stalled",
  nudge_sent: "Nudge sent",
  flag_qualified_waiting: "Marked Qualified, waiting",
  flag_claimed_idle: "Marked Claimed, idle",
  slots_offered: "Interview slots offered",
  booking_link_sent: "Booking link sent",
  interview_booked: "Interview booked",
  interview_rescheduled: "Interview rescheduled",
  interview_slot_released: "Interview slot released",
  outreach_sent: "Outreach sent",
  import_unresponsive: "Marked Unresponsive",
  opted_out: "Candidate opted out",
  opt_out_lifted: "Opt-out lifted",
  erasure_requested: "Erasure request received",
  tone_hint: "Tone hint",
  run_error: "Agent run error",
  template_sent: "Template sent",
  system_turn: "System turn",
};

export const countryLabel: Record<string, string> = { ES: "Spain", MX: "Mexico" };

export const senderLabel: Record<string, string> = {
  candidate: "Candidate",
  agent: "Screening Agent",
  human: "Team member",
  system: "System",
};

export function titleCase(value: string): string {
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
