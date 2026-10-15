# 0062. A Zone search that matches a city only offers the city's Zones

- Status: Accepted, refined by 0068 (a search that matches nothing offers the cities that have a Service Area)
- Date: 2026-10-11
- Refines: 0037 (where the pending choice comes from), 0042 (`search_zones` and Q45's city list) and 0048 (a correction's options stay live)

## Context
In a demo chat on real Haiku (2026-10-11, Application `app_01M4NHZ7MQY2XG4BJBYTXF8BRS`), a Mexican Candidate answered the Zone question with "Ciudad de Mexico en Tepito". Tepito is not a seeded Zone, so `search_zones("Tepito, Ciudad de México", MX)` matched the city only and returned its nine Zones. The Hermes session shows the agent's next step: no `record_answer` call, just the reply "Del listado de zonas de Ciudad de México, ¿cuál te queda más cerca de Tepito?". The skill tells the agent to pass every id of one city to `record_answer`, whose `_narrow` (Q45) writes the `answer_ambiguous` event that `pending_choice` turns into a list. With no call there was no event, so the message went out with no list. The Candidate asked "Cual listado?" and "No se en que zonas estan?" (the Jev router read both as confused), the agent searched again and still recorded nothing, and only a typed "Centro, ciudad de México" recorded the Zone.

The skill rule exists; Haiku skipped it once. The skill is at its size cap, and another sentence would not guarantee the step either. What decides that the city is known and no Zone is named is already code: the ranker's match tier.

## Decision
- **The search offers the list.** When `search_zones` matches on the city alone (the ranker's city tier: no Zone name or alias in the query) and every match is in one city, the backend offers those Zones exactly as `record_answer` would with the same ids: buttons for 2 or 3 (needs_choice), a list for 4 to 10 (Q45), nothing for more than 10 (the result has `more`, and the agent asks for the neighbourhood). It writes the same `answer_ambiguous` event (`{field: zone, options, message_id}`), so `pending_choice` attaches it to the agent's next message and a tap is recorded by the backend (0043). It counts no miss and stores no answer.
- **Only while the Zone is the question.** The Application is open, under agent control, in Screening, and the Zone is unanswered or this is a correction at the confirm step (0048). A Zone confirmation waiting for its Sí or No (0042) is never replaced: the agent passes the typed No as `null` first.
- **The agent is told.** The result gains `options_attached: true` when the options were offered (`contracts/tools.json`, additive), so the reply can say "elige en la lista" truthfully.
- **No duplicate offer.** When the agent then records the same ids in the same turn, `record_answer` finds the identical choice already open for the same Candidate message and writes no second event; its result is unchanged (needs_choice or the narrowing error with the options). A later message that leads to the same list is a new offer.
- **The lookup itself stays read-only.** The reference-table authorizer still covers the query; the one write happens afterwards, through the same code path `record_answer` uses. Candidate text never reaches a word list: the decision rests on the ranker's tier and the Zone ids, as before.

## Alternatives considered
- **A stronger sentence in the skill:** the rule was already there and Haiku skipped it; the skill is at its 13,200-character cap.
- **The backend attaches a list whenever the agent's reply mentions one:** reads the model's prose with a word list, which ADR 0042 removed from the backend.
- **`search_zones` records the Zone itself on one match:** the agent would lose the choice of a follow-up, and a single uncovered match needs the confirmation step anyway; only the city case has a list nobody else would send.
- **Make `record_answer` mandatory after a search (reject the next reply):** a Hermes-side turn check for one field; the list would still arrive a turn late.

## Consequences
- The demo chat's first reply would have carried the nine CDMX Zones as a list.
- `search_zones` writes an event in this one case; the sim's `UNOBSERVABLE_TOOLS` still holds (the event is the same one `record_answer` writes, and personas count it as before).
- Tests: `tests/tools/test_city_zone_offer.py`; the sim persona `zone_unknown_place_mx` with a stub rule that, like Haiku, asks without recording; the live flow `cdmx_unknown_place`, which also checks that the list comes with the first reply.
