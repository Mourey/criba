# 0058. A weekday's dates come from the backend's date line, and a date choice never holds today

- Status: Accepted
- Date: 2026-10-11
- Amends: 0055 (what the `[criba date]` line carries) and 0054 (what `verify_start_date` does with a two-date list)

## Context
The final goal pass ran the live start-date flows on Sunday 2026-10-11 on real Haiku 5.5, in Madrid and in Mexico City, twice (`start_date_choice`, `start_date_plain`). Every phrase that names the weekday of the day itself came back exactly 7 days early against 0054's rule ("a weekday is its next occurrence", so on the day itself the coming one is 7 days out) and the live checks (`expected_start` in `src/tests/live/flows.py`):
- "este domingo" was recorded as 2026-10-11 (today), not 2026-10-18;
- "el domingo de la próxima semana" was recorded as 2026-10-18, not 2026-10-25;
- "el próximo domingo" and "next Sunday" offered the buttons [2026-10-11, 2026-10-18], not [2026-10-18, 2026-10-25]. A date equal to today was offered as "next Sunday".

Every other weekday phrase passed. The `[criba date]` line of 0055 gave today and tomorrow only, so the model counted the weekday from today itself, and on the day itself it counted zero days. The skill's rule already said "next occurrence"; the model read today as one.

## Decision
- **The line lists every weekday's two dates.** After today and tomorrow, the plugin (`turn_date.date_line`) adds each weekday's next date strictly after today and the date a week later, in date order from tomorrow, under a label that says which phrase each column is for. Today's own weekday, the one the model counted from today, spells both readings out with their dates. On Sunday 2026-10-11:
  `Weekday dates, next (never today: "el", "este", "this") / a week later ("de la próxima semana"); a bare "próximo"/"next" is both: monday 2026-10-12 / 2026-10-19, ..., sunday 2026-10-18 / 2026-10-25 (today's weekday: "este"/"this" sunday is 2026-10-18, "de la próxima semana" 2026-10-25).`
  The model looks a weekday up and never counts days. The dates are computed from the backend's `today` (the same `get_application` value 0055 fetches once per turn), so the line's source and its once-per-turn cache are unchanged, and so is the `get_application` contract.
- **The skill points at it.** The rule reads "a weekday is its next occurrence (never today: `[criba date]`)". Its examples are unchanged (4 more characters in all; the prompt cap of `test_system_prompt_stays_small` holds at 13,198 of 13,200).
- **Code sends back a date choice that holds today** (`verify_start_date`). Two dates are two readings of one weekday (0054), and a weekday is never today. A list that holds today's date is `invalid_value` with the new reason `today_in_date_choice`, counted as no miss, with no event and no buttons, and a hint that tells the agent to take both dates from the line and call again without asking. This reads no Candidate text (0042): it checks the shape the agent sent against the rule the agent was given. One date of today ("hoy") is still accepted.

## Alternatives considered
- **Skill wording only** ("on the day itself, the weekday is 7 days out"): the rule already said "next occurrence", and the model still counted from today; the prompt is also at its cap.
- **The dates on `get_application`'s result:** a contract change, and those results are compacted once a turn ends (0045), so a turn that skips the call would again have no dates. The line is already on every call.
- **Code shifts a list holding today by 7 days:** code would guess what the agent meant. Sending it back keeps the reading the agent's and the rule code's.
- **Code also requires the two dates to be 7 days apart:** 0054 keeps the list open to other two-way readings, so only the reading that is never right, today, is refused.
- **The two columns with no per-phrase label and no same-day note:** the first live run with them (2026-10-11) fixed "este domingo" and "next Sunday", but recorded "el domingo de la próxima semana" as 2026-10-18; with the label alone, the second run recorded "este domingo" as today again. A one-call probe on real Haiku at the start-date turn took "el domingo de la próxima semana" as the first date 3 of 3 times without the same-day note and right 2 of 2 times with it.
- **Next dates only, the model adds 7:** about 35 fewer tokens, but "de la próxima semana" and the second button would still be arithmetic, across month and year ends.

## Consequences
- The line grows from 130 to about 580 characters (579 on a Sunday): about 170 more calibrated input tokens on every model call (1.5 tokens per 4 characters, `test_scenarios.py`), on the latest user message only, after the cached prefix.
- **Contract** (additive, for the merge notes): `tools.json` `tool_error.reason` names `today_in_date_choice` as a reason that counts no miss, and `answer_value_start_date`'s `$comment` says a list holding today gets it. No schema, event or API change.
- On the day itself, "el próximo domingo" now offers the Sundays 7 and 14 days out, as 0054's Consequences expected.
- **Live, real Haiku 5.5, Sunday 2026-10-11, own stack.** Final line: the full `start_date_choice` and `start_date_plain` flows passed (all checks), then a second run of the Sunday readings passed ("este domingo" 2026-10-18, "el domingo de la próxima semana" 2026-10-25, "el próximo domingo" buttons [2026-10-18, 2026-10-25]). In an earlier run the backstop fired: the agent passed [2026-10-11, 2026-10-18], got `today_in_date_choice`, and called again with [2026-10-18, 2026-10-25] without asking.
- The rule follows 0054's live checks: "de la próxima semana" is the next date plus 7. Said on a Sunday, many speakers mean the Sunday of the coming Monday-to-Sunday week (2026-10-18), and Haiku read it that way until the note. If real traffic shows Candidates mean that, 0054's rule is what changes, not this line.
- Tests: `tests/hermes/test_turn_date.py` (every weekday as today, both time zones, the line's format), `tests/screening/test_verify.py` and `tests/tools/test_record_answer.py` (`today_in_date_choice`), `tests/hermes/test_profiles.py` (the rule), `sim/stub_llm/test_engine.py` (the stub reads the new line) and `_date_line_gaps` in `sim/test_scenarios.py` (every call's line lists the right weekday dates on the sim clock); the `start_date_choice` and `start_date_plain` live flows.
