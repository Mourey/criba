# Sample conversations

Eight real conversations between a Candidate and the screening agent, each with the record it left: the transcript, the events, and the Application's JSON export (the screening fields, stage and Outcome, and the Summary, structured and prose).

| Sample | Capabilities shown | Outcome |
|---|---|---|
| [01 Qualified, in English](01-qualified-english.md) | AI disclosure from code; free text turned into values that code checks; two date buttons for "next Monday"; read-back; Sonnet Summary | Qualified, waiting for Approval |
| [02 Knockout, then Review](02-knockout-then-review.md) | Licence Knockout decided in code, explained, with a way back; a Review request for a person; Mexican Spanish | Knockout (`driver_license`), Review open |
| [03 Knockout reopened by a correction](03-knockout-reopened-by-a-correction.md) | A valid new answer reopens a Knockout; "tomorrow" from the local date; country follows the Zone; Summary flags the reversal | Qualified after a Reopen |
| [04 City list for a place that is not a Zone](04-zone-city-list.md) | `search_zones` matches a city; code offers its Zones as a list; the tap is recorded | Open at `driver_license` (drop-off) |
| [05 Texas, outside our cities](05-zone-outside-our-cities.md) | A place with no Zone gets the cities list, once; "None of these" is a Zone Knockout | Knockout (`zone`) |
| [06 Approval and Interview booking](06-approval-and-interview-booking.md) | Spain's Spanish; Approval by a person; `[system] approved` turn; slot list from the backend; booking with an `.ics` | Interview booked |
| [07 Frustrated, asks for a person](07-frustrated-escalation-and-takeover.md) | Jev reads frustration; tone hint and an Escalation; communication notes; Operator Takeover | Open, held by a person |
| [08 Language switch](08-language-switch.md) | Spanish to English mid-Screening; two answers in one message; Approval and booking in English; a gap shown honestly | Interview booked |

## Where they come from

- **Every Candidate is synthetic.** Samples 01, 02, 04, 05, 06 and 07 are scripted Candidates, the same scripts as the matching flows in [`live-smoke.md`](../evidence/live-smoke.md). In samples 03 and 08 the Candidate's lines were typed by hand as a test Candidate in the web app (on the Demo stage and in Test chat). None is a real applicant. Phone numbers are random ones from the web app or the script, addresses come from the synthetic seed, and the names are the script's (in 03 and 08, made up for the test).
- **The agent replies are real** Claude Haiku 5.5 output through OpenRouter, and the Summaries are real Claude Sonnet 5.5 output. Nothing in a transcript or a JSON block was written by hand.
- **One stack, one run per sample.** Each sample was read on 2026-10-12 from the demo stack `criba-demo` (http://localhost:8080) through the Operator API: the Application detail (`GET /api/applications/{id}`) for the transcript and events, and the export (`GET /api/applications/{id}/export`) for the JSON. The only exception is marked where it appears: sample 08 also shows flow 17 of the live smoke run (its own stack, same day), as a transcript only.
- **How buttons are shown.** As in `live-smoke.md`: `[buttons: A | B]` or `[list: A | B | C]` after the message that carried them, and `Candidate (tapped):` for a tap. A tap is stored as an option id; the transcript shows its label.
- **Redactions.** None were needed. No transcript contains a booking link or another signed token; the slot option ids (`so_...`) in the events are not secrets. No session cookie, password or API key is in these files.

The events in each sample hold ids, codes and canonical values, never the Candidate's words ([ADR 0035](../decisions/0035-no-candidate-free-text-in-events.md)). That is why the transcript and the record are shown side by side.
