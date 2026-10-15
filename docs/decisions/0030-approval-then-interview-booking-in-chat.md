# 0030. After Qualified: a Recruiter's Approval, then the Candidate books an Interview in the chat

- Status: Accepted
- Date: 2026-10-09
- Case: Grupo Sazón (defines the step after Qualified)

## Context
The owner defined what follows Qualified. When Screening is complete and everything checks out, the Application waits for one final human check. Only after a Recruiter approves does the Candidate get to schedule an interview. "Review" already means a Candidate disputing a Knockout, so this step needs its own term (`CONTEXT.md`: **Approval**).

## Decision
- **Qualified means waiting for Approval.** Approvals appear in the review queue, next to Reviews and Escalations.
- **Approval checks answers only.** Phase 2 collects no documents (licence or ID photos); that is deferred, see the README's Potential improvements.
- **Approval ends in Approved or Rejection.** An Approved Application stays open.
- **On Approved, the Screening Agent offers 3 Interview slots** as WhatsApp reply buttons (or as web chat buttons). The slots come from availability that Recruiters or Locations publish in the tracker. If the Candidate is outside the 24-hour window, the offer goes out as an approved template.
- **"None of these work"** sends a link to a small booking page in our web app, which shows all open slots.
- **A booking is an event on the Application.** Both the Candidate and the Recruiter receive a `.ics` invite. Nothing is written to Grupo Sazón's calendars (ADR 0012).
- **Booking is a backend tool** (`offer_interview_slots`, `book_interview`) with slot locking, so two Candidates can't take the same slot.

## Alternatives considered
- **A third-party booking link (Cal.com or Calendly):** booking data would live outside our database, and we would need their webhooks.
- **A booking page only:** a slower extra step for a Candidate who is already in the chat.

## Consequences
- The interview-scheduling bonus is covered.
- Hiring Outcomes after the Interview (hired, no-show) come in a later part of the case. Until then, an Approved Application stays open.
- Syncing with the client's calendars is listed as an improvement.
