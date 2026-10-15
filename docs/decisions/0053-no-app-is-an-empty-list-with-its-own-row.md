# 0053. "No app" is the empty platforms list, offered as a row from the first question

- Status: Accepted
- Date: 2026-10-10
- Refines: 0037 (step 3 of the pending choice), 0042 (what the agent passes for the platforms) and 0043 (which taps the backend records)

## Context
In a demo chat on real Haiku (2026-10-10, Application `app_01M4KH467FPDWWZN15J6KA42R4`), the Candidate said "tengo 10 años repartiendo como independiente". Then, asked for the delivery apps, they answered "Por mi cuenta", "ninguna de ellos" and "Nope con ninguna de ellas". The Hermes session shows what the agent did:
- after "Por mi cuenta" it asked a follow-up without calling, which the skill allows once;
- after "ninguna de ellos" it called `record_answer(experience_platforms, null)`, and the backend counted an `unresolved` miss;
- after the third answer it called `record_answer(experience_platforms, [])`, which was accepted.

The empty list was always valid. `verify_codes` lets only the platforms be empty, and the contract's `$comment` says "none is an empty list". But the plugin strips `$comment` from the tool schema, and the skill never said it. Before 0042 a word list mapped "none" in code (spec 006a, "synonyms and 'none' removed"), and nothing took its place. The question also had no buttons: step 3 of 0037 attached fixed options to the licence, availability and schedule only. The platform options reached the agent only from the second miss, in a tool result the channel never turns into buttons.

## Decision
- **The platforms question is a list from the first ask**: step 3 of the pending choice covers `experience_platforms`. Its rows are the six platform codes plus a last row, "Ninguna app" (es-ES, es-MX) or "No app" (en), with the id `none`.
- **`none` is not a platform code.** The contract's `platform_value` enum is unchanged. A tap on `none` is recorded by the backend (0043) as `[]`, the contract's value for no app. A typed bare `none` from the agent stays `invalid_args`, and its hint now names `[]`.
- **The agent maps "no app" to `[]`.** The skill's interpretation rules say "no app (on their own) `[]`", with the example "por mi cuenta" []. The `unresolved` hint for the platforms says the same, so a `null` the agent sends anyway is put right on the next answer.
- **Stored as `[]`, read as "no apps".** The years stay separate, so the record reads "10 years, no apps". With 0 years the question is still skipped (`experience_platforms_skipped`), and the platforms stay null.

## Alternatives considered
- **A `none` code in the contract's enum:** a frozen-contract change, and `["none", "glovo"]` would then need a rule. The empty list already means no app.
- **Treat `null` as "no app" when the years are above 0:** `null` means "could not map it". It would turn "no me acuerdo" into a stored answer.
- **Skill wording only:** the typed path depends on the model either way, but the tap gives the Candidate a path that needs no reading at all. The skill's size cap leaves room for about 50 characters, which is what the rule and the example take.

## Consequences
- A Candidate who delivered on their own can tap "Ninguna app" at the first question. A typed "por mi cuenta", "ninguna" or "no app" is `[]` on the first answer.
- The platforms question carries a seven-row list on WhatsApp and the web chat. Several apps are still typed, as with availability.
- Tests: `tests/screening/test_verify.py`, `tests/tools/test_pending_choice.py`, `tests/tools/test_tap_answer.py`, `tests/hermes/test_profiles.py`, the stub flow `test_no_app_is_recorded_as_the_empty_list_on_the_first_answer`, and the persona `platforms_none_mx`.
