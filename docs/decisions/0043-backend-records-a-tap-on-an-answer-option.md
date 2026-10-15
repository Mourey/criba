# 0043. The backend records a tap on an answer option; the agent obeys the result

- Status: Accepted
- Date: 2026-10-10
- Refines: 0037 (what a tap does after it reaches the agent) and 0042 (who calls `record_answer` for a tap)

## Context
0037 sends a tap to the agent as the option's `id`, and the agent calls `record_answer` with it. The real-model run of 2026-10-10 (`docs/evidence/model-comparison.md`) showed the weak point. Haiku 5.5 received the bare id `zone_mx_puebla_centro` as a user message, read it as a code the Candidate had typed ("no necesito códigos: elige tu zona tocando una de las opciones", which echoes the SOUL rule "Never ask for, accept or repeat an id"), recorded nothing, and looped to the 60-turn limit and an Escalation. The same tap on the same list was recorded in another run, so the failure is intermittent.

A tap needs no interpretation. The id is one of the options the backend itself offered (0037), so its meaning is fixed: the field, the structured value, and the shape `record_answer` takes (0042). Leaving it to the model adds a round trip, a chance of a miss, and a stall.

## Decision
- **The router records the tap before the agent runs.** When an inbound message is a tap (`reply_option_id`) on one of the pending **answer** options (0037 steps 2 and 3: an ambiguous Zone's options and the Zone confirmation, a Zone list, a fixed field's options), `criba.tools.tap_answer` maps it to the field's structured value and calls the same `record_answer` the agent would, through `call_tool`: the id in a list for the list fields and the Zone, the licence's `yes` or `no` as is, and the `no` of a Zone confirmation as `null`. Verification, Knockouts, misses and events are unchanged, and the answer event references the tap's message id.
- **The agent receives the result, not the id.** The run input is `[tap result] ` followed by the `record_answer` result JSON. The Screener skill says to obey it as that tool's result (ask `next_field`, send a Knockout's `message` exactly, re-ask after `zone_not_confirmed`) and not to call `record_answer` again.
- **Everything else is as before.** A tap on a slot or on "other", a tap that is not a pending answer option, a closed or taken-over Application, or any failure of the recording reaches the agent as the bare id. The router never raises on it.
- **A Knockout written by the recording belongs to the run's reply.** The turn carries the last event id from before the recording, so `link_knockout_message` still links the Knockout event to the agent message that carries its text (0035).
- **Typed answers are unchanged.** The agent still interprets typed text (0042), including a typed Sí or No for the Zone confirmation.

## Alternatives considered
- **Make the input clearer and leave the call to the model** (`[tap] Centro, Puebla (zone_mx_puebla_centro)`): fewer stalls, but a model that skips the call still stalls the Candidate. The real run showed the model ignoring a clearer rule. Code does this reliably.
- **Record in the agent's `get_application`**: the read tool would write. No.
- **Record the tap and send the agent only a bare "ok"**: the agent would need `get_application` to learn `next_field` and a Knockout's text. The result carries both and saves a tool round trip.

## Consequences
- A tap turn costs one model call fewer on average (no `record_answer` round trip) and can no longer stall on the id.
- The history in the Hermes session holds the result as a user message with no matching tool call. The skill says so in one sentence.
- The stub model reads a `[tap result]` input as a `record_answer` result (`sim/stub_llm/engine.py`), so its existing rules after `record_answer` apply unchanged.
- The Screener skill loses "Record the tapped option id" in three places and gains one rule.
- Tests: `tests/tools/test_tap_answer.py`, `tests/channel/test_tap_recording.py`.
