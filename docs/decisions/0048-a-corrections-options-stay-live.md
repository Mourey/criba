# 0048. A correction's options stay live while the field is already answered

- Status: Accepted
- Date: 2026-10-10
- Refines: 0037 (step 2 of the pending choice) and 0043 (which taps the backend records)

## Context
On real Claude Haiku 5.5 the persona `confirm_correction_mx` stopped at stage `confirm` in 3 of 4 runs. The Hermes session traces of the 2026-10-10 rerun show the path. At the summary the Candidate writes "no, mi zona es La Paz". Haiku calls `record_answer(zone, [...])` with the three Puebla ids it found earlier, and the backend answers `needs_choice` and writes `answer_ambiguous` with the three options, as it does on a first answer. Haiku then writes "Elige La Paz en la lista de zonas", but the message carries no buttons. 0037 step 2 attaches the options of the latest `answer_ambiguous` only while its field is still unanswered, and the Zone was answered before the summary. The Candidate has nothing to tap and a tap would not be recorded (0043 uses the same rule), so the chat stalls at `confirm`. `qualified_es` stalled the same way on real Haiku when the model re-asked an already recorded Zone and offered Madrid's list.

The backend's own check for an open Zone choice (`_live_zone_choice` in `criba.tools.application`) already uses a different rule: the options count until the Zone is recorded again. The two rules disagreed only when the field was already answered.

## Decision
- **An ambiguity stays open until its field is recorded again.** The options of the latest `answer_ambiguous` are the pending choice when they came after any Reopen and after that field's last `answer_recorded`, while the Application is in Screening. A field still unanswered is the case 0037 named; a correction at the confirm step is the new one.
- **Its taps are recorded like the first time** (0043): the backend calls `record_answer` with the tapped id, the stage stays `confirm`, and the result carries `next_field: null` and the `answers` for the summary question.
- **Nothing else changes.** A Zone confirmation during a correction gets its Sí/No buttons, and its No clears them (an `answer_ambiguous` with no options) without counting a miss and keeps the earlier Zone. Leaving Screening (`finish_screening`, a Knockout, a Takeover) drops the options.
- **The Screener skill says to search a new Zone first on a correction** and to search the whole place the Candidate named; this is wording that saves a step, not the fix. The skill grows by no characters (an explanation elsewhere was shortened).

## Alternatives considered
- **Skill wording only** ("record the Zone the Candidate named, not the city's list"): the model would still be able to ask for a tap that has no buttons, and the stall would remain one model slip away. Code holds the state (0042).
- **Clear the old answer when a correction starts:** the record would show a missing Zone during the confirm step, `finish_screening` would answer `incomplete`, and a Candidate who changes their mind would lose the answer they had.
- **Narrow the options with the Candidate's words in code** ("La Paz" among the three): code would read free text, which 0042 rules out beyond `search_zones`.

## Consequences
- A correction that the agent turns into a choice can be finished by a tap, on every channel.
- The simulator driver answers a re-asked field with the persona's correction from then on (it used to send the first Zone again and undo the correction, an artifact of the driver, not the product).
- Tests: `tests/tools/test_pending_choice.py`, `tests/tools/test_tap_answer.py`; persona `confirm_correction_pick_mx` plays the path on the stub model.
