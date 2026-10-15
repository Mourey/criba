# 0033. A Knockout reopens only on a valid new answer to the failed criterion

- Status: Accepted
- Date: 2026-10-09
- Refines: 0016 (its way back) and 0021 (its Reopen rule, for Knockouts only)

## Context
Under 0021, any message within 30 days of a Knockout reopened the Application. The Knockout message (0016) tells the Candidate to reply "revisión" to ask a person to review the decision. That reply was itself a message, so it reopened the Application first, and `request_review`, which needs a closed Knockout, could no longer be called. The Review, the safeguard 0016 relies on for GDPR Art. 22, was unreachable from the chat. The W2 end-to-end run found it (`no_license_mx`).

A Knockout and an Abandonment differ here. An Abandoned Candidate who writes again wants to continue. A Candidate after a Knockout may be disputing the decision, bringing news that changes the answer, or just saying thanks.

## Decision
- **A Knockout reopens only on a new valid answer to the failed criterion, within 30 days.** Examples: "ya tengo carnet" passes the driver's-licence check; a Zone now inside a Service Area passes the Zone check.
- **Any other message reaches the Screening Agent with the Application still closed.** The router attaches the message to the closed Application and runs the agent on its session. It no longer reopens Knockouts on inbound.
- **The agent decides from the message:**
  - a dispute or a request for a person: `request_review`;
  - news that might change the failed answer: it asks the failed question again and passes the answer to `record_answer`;
  - anything else: a brief, kind reply, with no new Screening.
- **The Reopen happens in `record_answer`.** For the failed criterion's field on a closed Knockout within 30 days, a valid answer reopens the Application (stage back to the criterion, a `reopened` event) and records the answer, in one transaction. The summary is rewritten as `reopened`. An invalid or still-failing answer leaves it closed. Any other field is `application_closed`.
- **Unchanged:** an Abandoned Application reopens on any message within 30 days; past 30 days, a message starts a new Application; an Overturned Review reopens a Knockout with no time limit.

## Alternatives considered
- **Detect "revisión" in code before reopening**, as STOP is detected: catches only the keyword, not "eso está mal" or "quiero hablar con alguien", and still reopens on a thanks.
- **Reopen on any message, and let `request_review` accept a reopened Application:** the Review would sit on an open Application in Screening, mixing the dispute with a new attempt, and the record would no longer show the Knockout the Review is about.
- **Never reopen a Knockout from the chat (Review only):** contradicts 0016's invitation to write again when circumstances change, and puts every new licence through a Recruiter.

## Consequences
- The router can run the agent on a closed Application. Tools that need an open one keep refusing with `application_closed`, and `get_application` works as before.
- The Screener skill gains an "after a Knockout" rule, and `contracts/tools.json` describes the exception in `record_answer`, `application_closed` and `request_review` (descriptions only; no schema change).
- An answer that fails validation on a closed Knockout is not counted as a miss, so it never raises an Escalation by itself.
