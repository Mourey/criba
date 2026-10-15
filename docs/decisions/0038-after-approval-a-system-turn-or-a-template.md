# 0038. After Approval the router sends a system turn or a template; only the scheduler holds for quiet hours

- Status: Accepted, amended by 0046 (the Approval message skips quiet hours when the Candidate wrote in the last 60 minutes)
- Date: 2026-10-09
- Refines: 0030 (the Interview offer after Approval) and 0009 (the router decides the next action from events)

## Context
Under 0030, an Approved Application gets 3 Interview slots in the chat. Approval is a Recruiter's action in the web app, not a Candidate message, so nothing would start an agent run. The offer must also respect WhatsApp's 24-hour window and quiet hours. In wave 2's first cut both the channel and the scheduler held messages for quiet hours, with two copies of the rule. Settled as Q41 and integrated.

## Decision
- **The router turns the `approved` event into the next action.** Inside WhatsApp's 24-hour window (the web chat has no window), it starts a run on the Application's session with the fixed system turn `[system] approved`. Outside the window it sends the approved Interview-offer template, with the slot buttons and booking link from code, and starts no run.
- **A system turn carries no authority.** It only says "go": it states no facts, and the agent reads `approved` from `get_application`. `offer_interview_slots` returns `not_approved` unless the Application is Approved in the database.
- **System turns are logged, never shown.** Each is a `system_turn` event (added to `events.kind` by migration `0002_events_system_turn.sql`) and never appears in the Candidate-visible transcript.
- **The scheduler alone holds actions for quiet hours.** There is one rule, `criba.scheduler.quiet` (22:00–09:00 in the Candidate's local time: the Zone's, else the country's, else UTC). The channel no longer holds anything; in quiet hours the sender and router answer `not_now`. An Approval goes to `Scheduler.submit_action` (`src/backend/criba/scheduler/service.py`), which runs it at once, or in quiet hours holds it until 09:00 local and then dispatches it once to `Channel.handle_event`.

## Alternatives considered
- **The web app's Approval endpoint sends the offer directly:** bypasses the router, the window and the Takeover check.
- **A system turn that states the facts ("the Candidate is approved"):** the model would obey an injected claim; reading the record is the only source of truth.
- **Quiet hours in both channel and scheduler:** two rules that drifted; a held message could be held twice or not at all.

## Consequences
- The stub model gained a `[system] approved` rule, so the sim covers the offer.
- A Team message typed during a Takeover is a human reply, not proactive, so quiet hours don't hold it.
- Held actions were in memory at first; 0039 persists them.
