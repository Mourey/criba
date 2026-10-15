# 0037. Buttons come from the backend's pending choice, one at a time

- Status: Accepted
- Date: 2026-10-09
- Refines: 0030 (slot buttons) and 0026 (answers recorded through tools)

## Context
Several Screening questions have fixed options (availability, preferred schedule, driver's licence Sí/No), an ambiguous Zone offers 2–3 matches, and after Approval the Candidate picks one of 3 Interview slots (0030). If the model wrote the options, it could retype them wrongly, offer slots it was never given, or leave old buttons live after the question moved on. Settled before wave 2 as Q40.

## Decision
- **The backend derives the pending choice from stored records**, with no new table: `pending_choice(conn, application_id) -> Interactive | None` in `src/backend/criba/tools/`. Priority order:
  1. unexpired, unbooked slot offers, as buttons;
  2. the options of the latest `answer_ambiguous` event whose field is still unanswered (written by `record_answer`'s `needs_choice`);
  3. the fixed options of `next_field`, labelled in the Candidate's language.
- **The channel attaches it.** After each run, the sender attaches the pending choice to the agent's last outbound message as `interactive`. The agent writes only the question text and never retypes options.
- **One pending choice at a time.** A newer interactive message supersedes the last; a tap on superseded options returns `409 option_expired` (`contracts/webchat.openapi.yaml`).
- **A tap reaches the agent as the option's `id`.** It is stored as a `button_reply` message, and the agent calls `record_answer` for an answer option (validated like typed text) or `book_interview` for a `slot_ref`.
- **No slot buttons after a booking.** Once the Application holds a booking, slot offers are not offered again unless `offer_interview_slots(reschedule=true)` ran after that booking.

## Alternatives considered
- **The model writes the buttons in a tool call:** the options would come from the model, not the record, and could drift from what the validators accept.
- **A `pending_choices` table:** a second copy of state already implied by slot offers, events and the next field.

## Consequences
- Buttons are the same on WhatsApp and the web chat, and always match what the backend will accept.
- Taps are still validated: a tapped option goes through the same tool as typed text.
- A stale clock can no longer keep slot buttons alive on the booking confirmation.
