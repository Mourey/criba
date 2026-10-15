# 0057. The Application's country follows the recorded Zone

- Status: Accepted
- Date: 2026-10-10
- Refines: 0021 (an Application belongs to every covering Location), 0042 (what `record_answer` does with a Zone) and 0047 (Spanish follows `country`)

## Context
In a demo chat on real Haiku (2026-10-10, Application `app_01M4KTA41DHX2BA3D5M4BEE8EK`), an English-speaking Candidate opened the web chat as Mexico (`country: MX`) and wrote "I live in Chamberi". `search_zones` found `zone_es_madrid_chamberi` (its row carried `country: ES`), and `record_answer(zone)` recorded it with the Madrid Locations (Chamberí, Salamanca). The Application kept `country: MX`.

The country is set once, when the conversation starts (the web chat's `country`, or the phone prefix), and several things read it:
- the Spanish variant: `_country_language` in `criba.tools.application`, `language_of` before any language is stored, the Jev router's `spanish_for` (0051), the copy (`copy_language`: nudges, the opt-out confirmation, the Qualified close, the AI disclosure) and the Knockout template's locale;
- the skill's wording ("Spanish follows `country`": carnet or licencia, moto or motoneta);
- the time zone fallback when no Zone is known (`candidate_timezone`, quiet hours, the Summary notes);
- the Recruiter list's `country` filter.

So a Mexican chat that lives in Madrid would, on a later Spanish turn, be addressed in es-MX, with Madrid Locations, Spanish slots and a Mexican Knockout template.

## Decision
- **The Zone decides the country.** When `record_answer` stores a Zone (`repo.record_answer`, through `_follow_zone_country`) whose country differs from the Application's, the Application takes the Zone's country in the same transaction. It applies wherever a Zone is recorded: the agent's call, a tapped Zone option (0043), a Knockout on an uncovered Zone, a Reopen after a Knockout (0033) and a correction at the confirm step (a correction back to the first country moves it back).
- **The language variant follows.** A Spanish language (`es-ES`, `es-MX`) becomes the new country's Spanish, on the Application and on the Candidate (whose language is that of the latest exchange). `en` stays `en`, and no language yet stays unset (it is then read from the new country). This is the same rule as 0047 and the Jev router's (0051), applied once more at the moment the country changes, so the router's next decision, `get_application` and the copy agree.
- **Codes only on the record (0035).** The zone's `answer_recorded` event carries `country_changed: {from, to}`, and `language_changed: {from, to}` when the variant moved. No new event kind: the change is part of recording the Zone, and a new kind would mean rebuilding the `events` table for its CHECK constraint.
- **The agent hears it in the same turn.** The `accepted` result of `record_answer` carries `country` only when it changed, so the next question in that turn already uses the new country's words. `get_application` returns the new country from the next call.
- **The Knockout template** is built from the record just written, so an uncovered Zone in the other country gets that country's template.
- **The Candidate's country is unchanged.** It records where the phone (or the chat) came from, and a later Application starts from it again until its Zone is recorded.

## Alternatives considered
- **Leave the country as opened and derive everything from the Zone:** every reader of `country` (copy, Jev, templates, filters, time zone fallback) would need a Zone lookup first, and the ones that run before a Zone is known still need the country.
- **A `country_changed` event kind:** clearer in the event list, but a contract change that rebuilds `events` (as migration 0002 did) and touches every consumer of the kind list (the persona schema, the web types). The data on `answer_recorded` says the same with an additive change.
- **Ask the Candidate to confirm the country:** a question the Zone already answers; the Zone search already ranks the opened country first and names the other country in its row.
- **Refuse a Zone in another country:** Criba hires in both countries, and the Candidate's home decides where they would work.

## Consequences
- In the demo chat's case the Application would now be `country: ES`, `language: en`, with Madrid Locations and Madrid's date and copy.
- **Contract** (additive, for the merge notes): `tools.json` `record_answer_output.accepted` gains the optional `country`; `api.openapi.yaml` `EventData_answer_recorded` gains the optional `country_changed` and `language_changed`. `src/web/src/api/schema.d.ts` was regenerated from it (`npx openapi-typescript`, 7.13.0), an additive diff only; nothing in `src/web` reads the new fields. `schema.sql` is unchanged.
- The Application detail API still shows the Candidate's country, not the Application's; the event says when they differ.
- Tests: `tests/tools/test_zone_country.py`, the sim persona `zone_country_tomorrow_en` (with `final_country` in the persona schema and `sim.asserts.check_stage`), and the live flow `zone_country`.
