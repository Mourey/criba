# 0044. Code guarantees the AI disclosure in the first message

- Status: Accepted, refined by 0068 (the privacy notice link is guaranteed in the first message too)
- Date: 2026-10-10
- Refines: 0012 ("every conversation discloses AI use at its start") and 0006

## Context
0012 requires every conversation to disclose AI use at its start (EU AI Act, from August 2026). The Screener skill and `SOUL.md` tell the agent to do it, and the opening template said "Soy el asistente virtual de Grupo Sazón" with no "(IA)". On the real model, one Haiku greeting copied the template and said "asistente virtual" alone; a legal duty should not depend on a model copying a sentence.

## Decision
- **The router checks the first agent message of each Application.** In `Router._run_and_reply`, before the reply is sent, `with_ai_disclosure` (`criba.channel.copy`) looks at the text and at the Application's earlier outbound messages. If none of them says it is an AI (a standalone "IA" or "AI", "inteligencia artificial", "artificial intelligence", or "bot"), then:
  - the first "asistente virtual" becomes "asistente virtual (IA)", or "virtual assistant" becomes "virtual (AI) assistant";
  - with neither phrase, a fixed sentence in the Candidate's language leads the message ("Soy un asistente virtual (IA) de Grupo Sazón.").
- **"Asistente virtual" alone is not a disclosure.** It does not say the sender is not a person.
- **Said once.** An earlier disclosing message, from the agent or the fallback, ends the check. A system turn after Approval is never the start of a conversation and is not checked.
- **The skill's opening template says "(IA)"**, so the guard normally changes nothing. The sentence is SYNTHETIC copy until Grupo Sazón supplies its own (`channel/copy.py`).

## Alternatives considered
- **More skill text:** the skill already said it, and the model is the thing being guarded against.
- **Prepend the sentence to the first message every time:** the same words twice in the usual case.
- **Send the disclosure as its own message:** a second bubble before the greeting, and a possible quiet-hours or window refusal separating the two.

## Consequences
- The agent text and what the Candidate reads can differ by this phrase; the Hermes session history keeps the model's own wording.
- A reply the guard changes can exceed the skill's 300-character target by the sentence's length.
- Tests: `tests/channel/test_copy.py`, `tests/channel/test_tap_recording.py`.
