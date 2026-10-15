// Copy for the Recruiter's Today page (/today). Terms follow CONTEXT.md; no cost figures here.

export const heading = "Today";
export const stubSentence =
  "The Applications that need you today will appear here: Approvals, Reviews, Escalations and booked Interviews.";

// The four sections, in page order.
export const approvalsTitle = "Approvals waiting";
export const takeoversTitle = "Takeovers waiting on me";
export const interviewsTitle = "Interviews today";
export const qualifiedTitle = "Recently Qualified";

// Table captions (read by assistive technology, not shown).
export const approvalsCaption = "Approvals, oldest first";
export const takeoversCaption = "Takeovers on my Claims";
export const interviewsCaption = "Interviews booked for today, by start time";
export const qualifiedCaption = "Qualified Applications, newest first";

// What each section says when it holds nothing: four distinct sentences.
export const approvalsEmpty = "No Approvals waiting. Qualified Candidates that need your Approval will appear here.";
export const takeoversEmpty = "No Takeovers waiting on you. Applications you have Claimed with a Takeover in progress will appear here.";
export const interviewsEmpty = "No Interviews today. Booked Interviews at your Locations will appear here on the day.";
export const qualifiedEmpty = "No Qualified Applications yet. Candidates who finish screening will appear here.";

// Column headers.
export const candidateHeader = "Candidate";
export const stageHeader = "Stage";
export const flagsHeader = "Flags";
export const waitingSince = "Waiting since";
export const heldByHeader = "Held by";
export const updatedHeader = "Updated";
export const timeHeader = "Time";
export const locationHeader = "Location";
export const actionHeader = "Action";

// Actions and links.
export const approve = "Approve";
export const reviewQueueLink = "Review queue";
export const allApplicationsLink = "All Applications";
export const moreInQueue = (shown: number) => `The oldest ${shown} are shown; more in the Review queue.`;

// Holders and names.
export const you = "You";
export const someoneElse = "Someone else";
export const unnamedCandidate = "Candidate (name not given)";

// Live status after an Approval, and the fallback when the server gives no message.
export const approvedStatus = (name: string) => `Approved ${name}`;
export const approveFailed = "Could not approve. Try again.";
