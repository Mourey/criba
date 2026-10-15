# 0076. The Zone funnel does not depend on the agent's search arguments, and a Zone question the agent leaves unhandled is settled by code

- Status: Accepted
- Date: 2026-10-12
- Refines: 0068 (when the funnel is offered), 0063 (misses counted by the backend) and 0042 (the agent interprets, code verifies)

## Context
In the owner's demo on real Haiku (2026-10-12, Application `app_01M4RPWTFKGP1CKJ48TS0MS1K7`, an MX web chat in English, Demo stage persona `zone_texas_funnel_en`) the Candidate wrote "I live in Texas!" seven times. In most turns the agent called `search_zones` twice, with `country` `ES` and then `MX`; both found nothing, and the agent answered in prose ("Texas isn't one of our zones..."). In the others it called no tool at all. It never recorded `null`. After `stage_changed → zone` the record holds one `tone_hint` and nothing else: no `answer_invalid`, no funnel, no Knockout, no Escalation. A loop.

ADR 0068 offers the funnel when `search_zones` finds nothing and "the search carries no `country` filter", or when the agent records `null`. It excluded filtered searches so that an agent exploring ("is it in Spain?" on a Mexican chat) does not trigger the funnel on a place that is a Zone in the other country. Haiku filtered every search and never recorded `null`, so neither door opened. The miss count (Phase 1 section 4.2, ADR 0063) only moves when the agent calls `record_answer` or when Jev reads a message as unreadable, so it never moved either.

The agent's first question, "Which barrio and city do you live in?", came from the model, not from code: the reply followed `record_answer(full_name)`, whose accepted result carries no wording, and the `[zone]` note is not on that turn. The Screener skill's Language rule named the Zone words for Spain ("barrio y ciudad") and Mexico ("colonia y ciudad") but none for English.

## Decision
- **An empty search counts whatever the filter, when the place is no Zone anywhere.** `search_zones` also ranks the query over every country's Zones when a `country` filter found nothing. The funnel is offered when the query matches no Zone in any country and the search had no filter or the Application's own country as its filter. A search of another country alone offers nothing in the tool (the agent may be exploring; ADR 0068's intent), and an own-country search for a place that is a Zone in the other country ("Madrid" on a Mexican chat, ADR 0057) is not a no-match.
- **The backend remembers what the searches for a message found.** Per Application and Candidate message, one bit: whether any search for it matched a Zone anywhere (`criba.tools.zone_searches`, process memory, ids and a boolean only, ADR 0035). The record has no place for it: the lookup is read-only and the trace keeps no results (0069). The backend is one process; a restart forgets the bits, and then the second rule below still applies.
- **A router backstop settles the Zone step after a typed run that did nothing with it.** After a run on a typed Candidate message read while the Zone is the next field (open, agent control, stage `zone`, not voice, not spoken, no Zone confirmation waiting, no newer message), when no answer event (recorded, a miss, options offered) references that message, code acts, through the same functions the tools use:
  - the cities question is open for an earlier message and a search for this one matched no Zone anywhere: the Zone Knockout (0068's second answer), and its template is the reply, an empty run included;
  - the funnel was never offered, and a search for this message matched no Zone anywhere, or this is the second typed message in a row with nothing done (the skill's one follow-up without a call, then `null`): `offer_funnel`, once per Application, the same `answer_ambiguous` event with `funnel: true`, and the router sends its fixed question as for a funnel the tool offered;
  - otherwise, once the funnel has been offered (or cannot be, as in a country with no or too many cities) and the agent still records nothing: a miss, `answer_invalid` with `reason` `unresolved` and the message id, so the third raises the existing `repeated_invalid_answers` Escalation. No new Escalation reason.
- **The `[zone]` note asks for a search with no country:** "look it up with search_zones, with no country, before you reply, whatever the place." The fix does not rely on it.
- **The Screener skill names the English Zone words:** "English is plain: "neighbourhood and city"." (8 characters net; 13,193 of the 13,200 cap.)

## Alternatives considered
- **Offer the funnel on any empty filtered search:** an exploring "Madrid" in `MX` on a Mexican chat would ask a Candidate who lives in a Spanish Zone which Mexican city they can work in.
- **Count which countries were searched for the message, and offer once all were:** the ranking over every Zone answers the same question in one call, and a model that searched only one country is caught by the backstop.
- **Keep the search result in the record (a new event kind, a column on `owed_turns`):** a contract and migration change for one bit that is only read right after the run that wrote it.
- **Read the search result from the tool-call trace:** it is best effort and keeps no result (0069).
- **Rank the Candidate's raw message in the router:** a second reading of Candidate text outside the agent, on words the agent did not choose as the place.
- **Knock out on the second unhandled message after the funnel, without a search:** a Candidate asking a question about the list would be closed. Code knocks out only on a search that matched nothing; otherwise it counts misses up to an Escalation.

## Consequences
- The demo chat's first "I live in Texas!" gets the cities question (the `MX` search offers it in the run); the second gets the Zone Knockout from the template. A model that never calls a tool gets the funnel at the second message and an Escalation at the third miss after it.
- A Candidate asking two off-topic questions in a row at the Zone question gets the funnel's question as the second reply. It is still a Zone question, and only "None of these" or a second place that matches nothing knocks out.
- **Not on voice (as 0068):** a spoken turn gets no backstop.
- **Known gap:** after a restart, the bit for a message searched before it is lost; that message falls under the second-message rule.
- **Contract:** none. `tools.json`, `schema.sql` and the OpenAPI files are unchanged; the events and their fields are 0068's and 0063's.
- **Deploy:** the backend and the Hermes image (the skill line) need a rebuild.
- Tests: `tests/tools/test_zone_country_filter.py` (the demo's searches, the own-country and foreign filters, the backstop's three outcomes, the edges), `tests/channel/test_zone_backstop.py` (the demo replayed through the web chat), `tests/tools/test_zone_funnel.py` (the filtered-search case now names a foreign country), `tests/hermes/test_profiles.py` (the English Zone words). No sim persona was added: a new persona also regenerates `src/web/src/stage/personas.json`, which another branch is editing.
