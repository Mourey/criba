# 0054. A start date that fits two days is passed as both, and code asks with two buttons

- Status: Accepted, amended by 0058
- Date: 2026-10-10
- Refines: 0042 (the `start_date` value and what code verifies) and 0037 (where a start-date choice's buttons come from, and which ambiguity is pending)

## Context
In the demo chat of 2026-10-10 on real Haiku, the Candidate said "El proximo domingo me queda bien" on Saturday 10 October. The Hermes session shows `record_answer(start_date, "2026-10-11")`: tomorrow. In Mexican and Spanish usage, "el próximo domingo" (and English "next Sunday") said within the week can mean the coming Sunday or the Sunday of the following week. Speakers disagree, and the most natural reading on a Saturday is often the later one. The skill's rule "a weekday is its next occurrence" made the model pick one reading and never ask. Code could not tell, because it receives only one ISO date (0042).

## Decision
- **The agent passes both readings.** `start_date` accepts a list of two different ISO dates as well as one date (`answer_value_start_date` in `contracts/tools.json`, an additive `anyOf`). The skill's rule reads: a weekday is its next occurrence; a bare "próximo"/"next" plus a weekday is a list of that date and the date 7 days later. Two examples go with it, both for Friday 9 October: "el próximo domingo" [2026-10-11, 2026-10-18] and "domingo de la próxima semana" [2026-10-18]. A first wording, "a weekday is its next occurrence ("próximo"/"next" one: list it and +7 days)", had real Haiku offer two Sundays for "este domingo" and for "el domingo de la próxima semana" in one of the two live runs with that wording. With "bare" and the second example, both were recorded with no question in the two runs since. "Mañana", "el lunes", "este domingo", the following week named outright, and a calendar date are still one date.
- **Code verifies and decides** (`verify_start_date`):
  - a reading that is not a calendar date is `invalid_date` and counts no miss, as for one date;
  - a past reading is dropped, and one reading left is accepted as if it came alone, `start_date_far` included;
  - two past readings are one `past_start_date` miss;
  - two readings left are `needs_choice`, with the dates as option ids in date order and labels in the Candidate's language ("Domingo 11 oct", "Sunday 11 Oct"; 17 characters at most, under the 20 a reply button allows).
- **The existing choice path does the rest.** The `answer_ambiguous` event carries the options. `pending_choice` attaches them as two buttons while the start date is unanswered (0037 step 2), or during a correction at the confirm step (0048). The backend records a tap with the date as the value (0043). A typed pick ("el 18") is the agent's single date, and recording it ends the choice.
- **A displaced choice comes back** (refines 0037 step 2). In one live run, Haiku offered the two Sundays and, in the same turn, re-asked the Zone with Puebla's three Zones. The Zone buttons replaced the dates, since only one choice is pending at a time. Once the Zone was tapped, the dates never came back, and the Candidate's typed "el de la otra semana" ended in three misses and an Escalation. The pending choice is now the newest ambiguity that is still open, taking only each field's latest ambiguity into account. When the newest one closes, an older open one for another field returns.
- **Code does not read the phrase.** It never parses "próximo" (0042). Which words fit two days is the agent's reading; the two dates and what happens to them are code's.

## Alternatives considered
- **The phrase kind** (`{"next": "sunday"}`), with code computing both dates: a second value shape whose date arithmetic duplicates what the agent already does from `today` and `weekday`, and that fits only one kind of ambiguity. A list of two dates also covers other two-way readings without another schema change.
- **Detect "próximo" in the Candidate's message in code:** the word lists 0042 removed, and "próximo lunes" can be said in a message that names a date.
- **Always take the later reading, or always the earlier:** either one is wrong for some Candidates, and the Candidate is never asked. A wrong start date reaches the Recruiter as a fact.
- **Skill wording only** ("ask which Sunday"): the model writes the question and the Candidate types an answer that must be read again. Buttons from code make the answer a tap.

## Consequences
- "El próximo domingo" costs one extra tap, and only when it fits two days. On the day itself ("el próximo domingo" said on a Sunday) the agent may still pass both, the coming one 7 days out and the next 14; the buttons then ask a question that most Candidates would not need. This is accepted until real traffic shows it matters.
- **Contract** (additive, for the merge notes): `answer_value_start_date` becomes an `anyOf` of a date or a list of two dates, with a new `iso_date` definition; `record_answer` gains an example; the `needs_choice` description names start dates. The stored answer, the `answers` schema, `EventData_answer_ambiguous` (whose `field` was already any answer field) and the API are unchanged.
- To stay under the prompt cap (`test_system_prompt_stays_small`), these were cut from the skill: two placeholder notes (in the FAQ and the templates), "in one or two sentences" in step 7 (the tone rules already say it), "until then `next_field` is all you need" in step 1, and some words of "pass what they said ... the backend checks them" in step 2.
- **Seen in the live runs, not caused by this change:** after "Mañana", Haiku often records today. It happened 4 of 4 times with the skill on `main` and 3 of 3 with this branch's skill. In those turns it skipped `get_application` and resolved the date from its history. When it called `get_application` first, it recorded tomorrow. This is left for a separate fix; the `start_date_plain` live flow reports it.
- Tests: `tests/screening/test_verify.py`, `tests/tools/test_tap_answer.py` (with the displaced choice), `tests/tools/test_record_answer.py`, `tests/hermes/test_profiles.py`, the stub flow `test_next_sunday_passes_both_sundays_and_asks_without_retyping_them`, `sim/stub_llm/test_interpret.py`, `sim/test_detect.py`, and the persona `next_weekday_choice_es`.
