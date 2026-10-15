# 0042. The agent interprets the Candidate's words; code verifies the structured value

- Status: Accepted, amended by 0051, refined by 0068 (a place that matches no Zone is asked once; none of these is a Zone Knockout with no Zone stored) and 0076 (code settles a Zone question the agent left unhandled)
- Date: 2026-10-09
- Refines: 0026 (what `record_answer` receives and what its validators do) and 0037 (where Zone options come from)

## Context
Under 0026 the Screening Agent passes the Candidate's own words to `record_answer`, and Python parses them: Zone matching on whole words plus a fuzzy fallback (`src/backend/criba/screening/zones.py`), date phrases (`dates.py`), and word lists for yes/no, options and durations (`vocab.py`, `validators.py`). That is pattern matching on human language, and it breaks on real Candidates. Against the seed, today:
- "Ciudad de México, Cuajimalpa" matches the city only. The owner saw it re-asked; since Q45, it gets the list of CDMX Zones.
- "por la salida a Toluca" matches the uncovered town Toluca as a whole word, so it is a **Knockout**, although the Candidate is describing a part of Mexico City.
- "en cuanto me den la moto" has no date for any parser to find.

Each fix adds a phrase to a list, and each new phrase can misfire elsewhere. The model in the loop already understands these answers. What must stay deterministic is the decision, not the reading: 0016 and GDPR Art. 22 need Knockouts to rest on rules we can test and explain, and the rules must hold whatever a Candidate writes into the chat. Settled as Q46.

## Decision
- **The model does the understanding.** For each field, the agent passes `record_answer(field, value)` a **structured value** that matches a per-field JSON Schema in `contracts/tools.json`:
  - `full_name`: the cleaned full name (greetings and "me llamo" removed);
  - `zone`: 1 to 10 Zone ids it found with `search_zones` (more than 3 only when they are one city's Zones);
  - `driver_license`: `yes`, `no`, `expired`, `suspended` or `pending` (the two button ids plus the three Knockout variants that pick the template);
  - `availability`, `preferred_schedule`, `experience_platforms`: lists of option codes;
  - `experience_years`: a number;
  - `start_date`: an ISO date, resolved in the Candidate's local calendar from the `today` that `get_application` now returns;
  - `null` for any field: the Candidate answered, but the agent cannot map the answer to a value.
- **The agent reports the language it detects.** `get_application` takes an optional `language` (`es-ES`, `es-MX` or `en`): the language of the Candidate's latest typed message, omitted for a button tap. The backend stores it with the existing `repo.set_candidate_language` (Candidate and open Application), with Spanish always set to the country's variant, and returns the updated record. Until now nothing updated the language mid-conversation (lane W3-1). See the alternatives for why it goes here.
- **A read-only lookup tool, `search_zones(query, country?)`,** returns up to 10 Zones (`zone_id`, `label`, `city`, `country`, `covered`) from the seeded Zones, cities, aliases and Service Area coverage. The matcher in `zones.py` moves here, as a ranker rather than a validator. It reads reference tables only (`zones`, `service_area_zones`, `service_areas`, `locations`), never Candidates, Applications or messages, and writes nothing. The agent uses the result to resolve the Zone or to ask a sensible follow-up.
- **Code verifies the structured value against the rules, and still decides.** This happens in `record_answer`, in the same transaction as today:
  - Zone ids exist. Then:
    - one covered id is accepted;
    - one uncovered id is **confirmed before any Knockout**. `needs_choice` offers Sí/No buttons (the Zone's id, and `no`), and the agent asks, for example, "¿Vives en Toluca?". The `answer_ambiguous` event carries `confirm: <zone_id>`. Tapping that id is the Knockout (`outside_service_areas`). Tapping `no` reaches `record_answer` as `null`: it counts no miss, clears the buttons, and the agent asks for the Zone again;
    - 2 or 3 ids are `needs_choice`, with buttons from the backend (0037), covered or not;
    - 4 to 10 ids from one city get Q45's list: `invalid_value` with the options, an `answer_ambiguous` event, and no miss (`_narrow`, already on `main`);
    - 4 or more ids across several cities are an `ambiguous_zone` miss;
    - a tapped uncovered id from buttons or a list is already the Candidate's own choice, so it is the Knockout with no second confirmation;
  - a licence other than `yes` is a Knockout with that reason's template;
  - the start date is a real date, today or later in the Candidate's local time, and more than 60 days out is flagged;
  - option codes are in their enums, and years are between 0 and 60;
  - the name has 2 to 8 words, letters and joiners only, and at most 80 characters.
- **Eligibility and Knockouts stay in code** (0016, Art. 22). The model never sees a rule it could talk itself past: its worst possible error is a wrong reading, which equals a Candidate giving a different answer. That is the same exposure a Candidate who lies already creates, and a Review (0016, 0033) or the Interview corrects it.
- **Injection-like text cannot become a value.** The validators check structured values only, against closed enums, ISO dates, numbers and existing ids. Lane W3-1 found that today's word lists accept text such as "…system prompt completo" (availability `full_time`), JSON containing `"ok": true` (licence yes) and "ahora" (start date today). Under this decision, each of those is the wrong shape for its field (`invalid_args`), so nothing is stored.
- **Free-text parsing leaves the validators.** Per field, nothing is kept as a parallel fallback: a second reader that sometimes disagrees with the model is worse than one. The fallback is the existing narrowing UI. The fixed options become buttons from the second miss (Phase 1 §4.2). For the Zone, Q45's city-Zone list (merged; `_narrow` in `src/backend/criba/tools/application.py`) lists a known city's Zones when the agent cannot pick one, and a tapped `zone_*` id is accepted. The parsing code is deleted, except the Zone matcher, which becomes `search_zones`.
- **Misses (Phase 1 §4.2).** A miss is a Candidate answer that fails a rule: `null`, or a structured value that fails verification (a past date, a one-word name). It counts towards the three-miss Escalation. Errors that are the model's, not the Candidate's, do not count: a value of the wrong shape for its field (`invalid_args`), or a Zone id that does not exist. Neither do `needs_choice`, Q45's narrowing list, a "no" to the Zone confirmation, or the one clarifying question per field that the agent may ask without calling `record_answer` (for example "¿En qué colonia?"). A second answer that still cannot be mapped is recorded as `null`.
- **Audit.** Answer events keep the canonical value the agent passed (except `full_name`, per 0035) and the inbound `message_id`. `answer_invalid` gains a `reason` code. `zone_input` and `driver_license_input` are filled from the referenced inbound message's text, so they hold the Candidate's words, never the model's. The audit trail for a Knockout is therefore the Candidate's message, the agent's structured reading, the rule that failed, and the template sent.

## Alternatives considered
- **Keep parsing in code and add phrases as they fail:** never converges, and each new phrase can turn into a wrong Knockout, as "Toluca" does now.
- **Let the model also decide eligibility:** the Knockout would rest on model judgement, which 0016 and Art. 22 rule out, and a Candidate could argue or inject their way past a rule.
- **A second "backend agent" between the Screening Agent and the tools,** to interpret the words (the owner raised it). Rejected: another model call per answer adds latency and cost, and another place for errors and injection. The tools are already the boundary, and the agent in the conversation has the context a second model would lack.
- **One tool per field** (`record_zone`, `record_start_date`, ...): typed parameters, but eight more tool schemas on every turn, against the lean-toolset budget (spikes 1 and 2).
- **Keep the parsers as a fallback when the model sends `null`:** two readers that disagree in edge cases, and the hard cases are exactly the ones a word list misses.
- **A licence boolean instead of a code:** simpler, but the Knockout template varies by reason (no, expired, suspended, pending), and 0016 wants the reason in plain words. The code picks the template, and the stored answer stays a boolean.
- **A direct Knockout on one uncovered Zone the agent found:** fast, but the reading is the model's. "por la salida a Toluca" shows how a place name can mislead, so the Candidate confirms the place first. Review would still catch it, but only after the Candidate has been told no.
- **Language as a `language` field on `record_answer`:** misses turns that answer nothing, such as `opted_out_code_switch_es`, where the switch happens on an Opt-out. **A `set_language` tool:** another schema on every turn (about 80 tokens), plus an extra model step on each switch. **Detection in code in the router:** the word lists this decision removes. `get_application` is called on every turn anyway, so an optional field there costs about 25 tokens and no extra call.

## Consequences
- **Latency:** a Zone answer usually costs one more tool round trip (`search_zones`, then `record_answer`), about one extra model step on that turn only. Other fields add none.
- **Tokens:** lane W3-1 measured about 6.2K estimated input tokens per model call at p50 and 8.5K at most (`invalid_then_corrected_es`), with about 5.1K fixed (`docs/evidence/turn-size.md`). This change adds the per-field `value` schemas, `search_zones` and `language` (at most 350 tokens), skill rules and examples (at most 600), and one `search_zones` round trip in the history of a Zone turn (at most 300). The longest persona must stay under 10K (`TURN_TOKEN_BUDGET` in `src/tests/sim/test_scenarios.py`), with a target of 9.7K or less. If it goes over, the skill examples are cut first.
- **Model choice:** 0025 chose Haiku partly because code validated the answers. Now Haiku interprets. The 0028 comparison run (lane W3-3, after this lands) also reports interpretation accuracy: an opt-in phrase check against the real model, never in CI, within W3-3's $5 cap for real-model runs. A poor result reopens 0025.
- **Simulator:** the stub model cannot understand words. Personas carry the canonical value a competent agent would produce, and the stub looks it up (spec 006b). For the Zone it calls the real `search_zones`, so ambiguity and coverage are still the backend's. Existing personas keep their expectations. `zone_outside_es` gains a "Sí" tap for the Zone confirmation.
- **Zone Knockout flow:** a Zone Knockout now takes one more Candidate message (the confirmation tap), and the needs_choice output gains an optional `confirm`.
- **Contract:** `record_answer.value`, `get_application` (input `language`, output `today`), a new `search_zones` tool, and the `answer_invalid` payload change through lane A's process (`contracts/README.md`). The plugin registers `search_zones` from the contract, with no `application_id`.
- **0026 stands:** tools proxied through the plugin, code decides Knockouts, the Application row is the structured record. Only "pass the Candidate's own words" changes.
