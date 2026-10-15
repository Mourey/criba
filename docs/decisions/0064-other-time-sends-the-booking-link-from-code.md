# 0064. A tap on "other time" sends the booking link from code

- Status: Accepted
- Date: 2026-10-11
- Refines: 0050 (which taps the backend answers before the agent runs), 0030 ("none of these work" sends a link) and 0044 (code guarantees what the model is told to do)

## Context
In the demo chat of 2026-10-11 on real Haiku, the slot buttons after Approval came with "Otro horario". The Candidate tapped it. 0050 left that tap to the agent as the bare id `other`; the Screener skill says "None of these work: point to `booking_url`". The agent instead asked "¿Qué día y hora te vendrían mejor para la entrevista? Lo anoto y lo coordino…" and re-showed the same slot buttons. No link reached the chat, and the promise to note the time was false: nothing in the system takes a free-text time.

The cause is the same as in 0044 and 0050: a duty the product depends on was left to a model reading an opaque id (`other`) and remembering a sentence of the skill from two turns earlier. The `booking_url` was in the history only as an `offer_interview_slots` result that 0050's trim shortens, so the agent no longer had the link to point to.

## Decision
- **The backend answers the tap.** `tap_answer` (`criba.tools.choices`) treats the id `other` as the Candidate's whole answer, as it does for a slot (0050). It mints the link with `booking_url(app_id, now=...)`, the one token format and secret of 0040, and returns `{"ok": true, "value": {"status": "booking_link", "booking_url", "message"}}` after `[tap result] `. The `message` is fixed copy in the Candidate's language (`booking_link_message` in `criba.channel.copy`: es-ES, es-MX, en; SYNTHETIC until Grupo Sazón supplies its own) and ends with the link.
- **When it applies.** While a slot offer is live, and for an Approved Application with no Interview whose offer expired (the old message's button still sits in the chat, and the booking page lists every open slot). Not before Approval (`other` is also a platform answer there, still recorded as one), not after a booking, not under a Takeover or on a closed Application.
- **The link reaches the chat whatever the agent writes (the 0044 pattern).** The agent still runs, so its session records the tap and it may word the reply: the result's `message` is the text to send as given. In `Router.store_reply`, a reply to such a turn that does not contain the link is replaced by the `message`. An empty reply, and a failed run (which still opens its `technical_error` Escalation), send the `message` instead of saying nothing or "dame un momento".
- **Not on a voice Application (0059).** Voice is its own channel with no messaging between calls, and the Voice Screener never mentions a link: it tells a Candidate no slot suits that someone from the team will contact them. So `other` on a `voice` Application is left to the agent as the bare id, as 0050 left it.
- **The slot offer stays valid.** The tap writes nothing, so the slot buttons are still the pending choice and ride on the reply; a slot tapped afterwards is booked by the backend as in 0050.
- **No skill change.** The skill's "obey that JSON as the tool's result" already covers a `[tap result]`, and the skill sits at its character cap. `history_trim` shortens the result to its `status` and the "sent word for word" marker with no change (0050).

## Alternatives considered
- **Send the message from code and skip the run:** deterministic and cheaper, but the agent's session would never see the tap or the link, and a later "ok" or "¿y ese enlace?" would meet a history that does not contain them.
- **Skill wording only:** the failure that started this. The model has to connect `other` to a sentence in the skill and to a URL its history no longer holds.
- **Append the link to whatever the agent wrote:** keeps the false "Lo anoto y lo coordino" in front of the link.
- **Put `booking_url` in the `other` option's `id`:** ids stay short opaque codes (0037), and a signed URL does not fit WhatsApp's button id.

## Consequences
- "Otro horario" gives a working link on every channel and with any model reply; a reply that already contains the link keeps the agent's wording.
- Each tap mints a link valid for the usual `CRIBA_BOOKING_LINK_DAYS`; repeated taps send repeated links.
- The stub model relays the `message` of a `booking_link` result, as it does a Knockout's.
- Tests: `tests/tools/test_tap_answer.py`, `tests/channel/test_booking_confirmation.py`, `tests/tools/test_tap_answer.py` (the voice case), `tests/test_wiring.py`, `src/tests/sim/stub_llm/`, `test_approval_other_time_tap_sends_the_booking_link_and_the_slots_still_book` in the scenario suite, and the `other_time` live flow.
