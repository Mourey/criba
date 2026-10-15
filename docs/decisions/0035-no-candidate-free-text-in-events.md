# 0035. Events hold ids, codes and canonical values, never Candidate free text; hand-backs get their own table

- Status: Accepted
- Date: 2026-10-09
- Refines: 0009 (the record agents coordinate through) and 0020 (the hand-back recap)

## Context
The event log is the Application's history (0009): the router reads it, the web app shows it as the timeline, and the summary is built from it. Wave 1 copied the Candidate's own words into `events.data`: `raw_input` on answer events, the `note` on queue events, the Knockout `message`, and the Takeover hand-back `summary`. An Erasure request would then have to scrub the event log as well as the messages, and every new event kind was another place free text could hide.

Before wave 2 the contract settled the rule: `Event.data` is an open object of ids, codes and canonical values. After wave 2 the per-kind payloads were typed, and the existing writers and rows were brought into line.

## Decision
- **`Event.data` never holds Candidate free text.** It holds ids, codes and canonical values only (an answer's canonical value, a Knockout reason, a template name).
- **Free text is referenced by id.** Answer events (`answer_recorded`, `answer_invalid`, `answer_ambiguous`) carry the inbound `message_id`; queue events (`review_requested`, `escalation_raised`, `approval_requested`) carry `queue_item_id` and a reason code, and the note stays on the queue item; `knockout` carries the sent reply's `message_id` (linked by the router once sent); `takeover_handed_back` carries `handback_id`.
- **Hand-back summaries live in a `handbacks` table** (migration `0004_handbacks.sql`, matching `contracts/schema.sql`). It holds the recap the agent reads on its next turn and is erased with the Candidate's messages. `get_application` reads it from there.
- **Existing data was scrubbed** by migration `0005_events_no_free_text.sql`: hand-back summaries moved to `handbacks`, old Knockout texts matched to their message, the other free-text keys removed with `json_remove`. The withdrawal and Erasure-request notes are no longer kept anywhere.
- **The API strips free-text keys as a backstop** (`event_json` in `src/backend/criba/api/present.py`), so a stray key never reaches the timeline.
- **Payloads are typed per kind.** Each `Event.kind` maps to an open `EventData_<kind>` schema through `x-event-payloads` on `Event.data` in `contracts/api.openapi.yaml`. A test validates every written payload against its schema and checks that every kind in `schema.sql` has a mapping.
- **`SummaryStructured` is typed and open** (`additionalProperties: true`): the listed fields are always present, and the summary writer may add more.

## Alternatives considered
- **Keep free text in events and scrub on Erasure:** every reader and every new kind becomes an Erasure risk, and a missed key is a data-protection incident.
- **Encrypt free text inside events:** the same scattering, plus key management, for text that already lives in `messages`.
- **Keep the hand-back summary in the event:** it is free text (the team member's words, or a recap quoting the transcript), so it falls under the same rule.

## Consequences
- An Erasure request deletes messages, queue notes and hand-backs; the event log needs no scrubbing.
- An end-to-end marker test (`src/backend/tests/api/test_no_free_text_in_events.py`) drives answers, misses, a Knockout, a Review, an Escalation, a Takeover and hand-back, a withdrawal and an Erasure request, and asserts no event holds a marker.
- Readers that wanted the text (the sim assertions on the Knockout reply, the timeline) look it up by id.
