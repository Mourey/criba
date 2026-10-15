# 0045. What a finished turn left in the history is shrunk on the wire

- Status: Accepted
- Date: 2026-10-10
- Refines: 0041 (what the client-facing agent carries in its context) and 0010 (the Application, not the session, is the memory)

## Context
Every model call replays the whole Hermes session. A screening is one Candidate message per turn, and each turn leaves tool results and the model's hidden reasoning in the history. Both are useful only while their own turn runs: `get_application` says what to ask, `record_answer` says what is next, `search_zones` lists the Zones to record, and the next turn starts with a fresh `get_application` that the skill says to trust over the chat.

The real-model runs of 2026-10-10 put the longest persona, `zone_freeform_mx` (12 turns, a nine-row city list, the "¿Vives en Toluca?" confirmation), over the 10,000 input-token limit on Haiku 5.5 (5 to 8 calls, max 11,066), after the first round of cuts (`docs/evidence/turn-size.md`). In that session:
- the tool results of finished turns were about 5,300 of the longest call's 7,500 history characters (a nine-row list of 783, the `ambiguous_zone` error repeating it, 11 `get_application` results of 225);
- every assistant message with reasoning carried `reasoning_details`, about 1,400 characters of provider signature (8 messages in the 12-turn session), which Hermes replays to OpenRouter on every later call (`agent/transports/chat_completions.py`, `_route_replays_reasoning_details`).

Run on real Haiku 5.5 with each half on and off (`zone_freeform_mx`, max input tokens per call from Hermes's `agent.log`): neither 10,821; tool results only 10,139 and 11,036; reasoning only 10,739 and 10,831; both 9,441, 9,475 and 9,596. Neither half is enough alone.

Hermes can prune old tool results itself (`compression.proactive_prune_tokens`, `agent/context_compressor.py:3482`), but it rewrites the stored session and breaks the cache prefix on every commit (turn-size.md, round 1, cut 5). A request-time rewrite does neither.

## Decision
- **The criba-tools plugin shrinks the history of each request** with a second `llm_request` middleware (`src/hermes/plugins/criba_tools/history_trim.py`, next to the system-prompt trim). On the copy of the request that goes out, for every message before the latest user message:
  - a **tool result** (and a `[tap result]` user message, 0043) over 90 characters becomes the keys that say what it was: `ok`, `error`, `reason`, `attempt`, `value.status` (else `value.stage`), `value.field`, plus `options` and `confirm` when the options are short (at most 200 characters), so a Candidate who answers a Zone confirmation or a short choice in words still finds the ids. A long list (ten Zones) goes: the skill searches again, and the model's own `record_answer` call, which is kept, still has the ids it sent;
  - an **assistant message** loses `reasoning_details`, `reasoning_content` and `reasoning`; its text and its tool calls (names and arguments) stay.
- **The turn in progress is untouched**, including its reasoning, which the provider needs back with the tool calls of the turn that is running. User and assistant text, the system message and the tool schemas are never changed.
- **The stored session is untouched.** Only the request changes, and the rewrite is a pure function of the messages: an old message is rewritten the same way on every later call, so the provider's prompt cache keeps its prefix up to the previous turn.
- **It fails open.** Content that is not a JSON object, a list of non-text blocks or any shape it does not know is left as it is, and a callback that raises is skipped by Hermes.

## Alternatives considered
- **Hermes's own pruning:** rewrites the stored session and the cache prefix on each commit (see Context).
- **Smaller tool results at the source** (`contracts/tools.json`): a result is read once in its own turn and then lives in the history, so the history is where the repeat is. Shorter results cut the turn in progress by tens of tokens and change a frozen contract. Not done.
- **Drop the finished turns' tool calls and results altogether:** the model would stop seeing itself call `get_application` first, and the skill's "every turn starts with `get_application`" would be unsupported by its own history.
- **Turn reasoning off:** a behaviour change on the model that screens Candidates, to save what stripping the finished turns already saves.

## Consequences
- On real Haiku, `zone_freeform_mx` goes from a max of 10,821 to 9,441 and 9,596 input tokens per call, with no call over 10,000 in the final runs (turn-size.md, "Real usage"). The stub's calibrated longest call for the persona goes from 9,328 to 7,877.
- The cache hit falls a little (about 97.5% to 96.3% of the prompt): the prefix now breaks at the start of the previous turn instead of never.
- The model no longer sees the hint, the options or the answers of an earlier turn's results. It does not need them: a miss's hint is used in its own turn, the answers are in the latest `get_application` at the confirm step, and `next_field` is in every fresh result.
- The scenario suite fails if a tool result over 300 characters from a finished turn reaches the stub (`_history_leaks`), and `zone_freeform_mx` has its own assertion against the 9,500 target.
- Tests: `tests/hermes/test_history_trim.py`, `tests/hermes/test_prompt_trim.py` (registration).
