# Turn size: input tokens per model call

Measured 2026-10-09 by lane W3-1 on the sim stack (backend, Hermes `v0.21.6` on the
`criba-recruiter-sim` profile, stub model), again by lane 006b after the structured answers
(see "After spec 006b" below), and after the 006a clean-up (see "After the 006a clean-up"). Limit: at most **10,000 input tokens** per model call (TODO "Watch
the turn size"), with a target of **9,700** for the longest call. `src/tests/sim/test_scenarios.py`
fails any call over the limit, and the turn-size report over the target.

**Read the sections in order, and read the last one first.** Everything up to "Real usage" uses
the plain characters / 4 estimate, which turned out to read 1.5 times too low. "After the
real-usage fix" at the end calibrates the estimate against OpenRouter's real counts and reports
the cut that brings the longest call under the limit (14,460 to 9,367 calibrated tokens).
"Round 2" at the very end is the last cut: it shrinks what finished turns left in the history,
and "After round 2" under "Real usage" has the real counts (no call over 10,000 in 329, max
9,997).

## Method

- **What is measured:** the prompt Hermes actually sends to the model, read from the stub
  model's request log (`/__stub/requests`): every message (system prompt, Candidate turns,
  agent replies, tool calls and tool results) plus the tool schemas. One number per model
  call. A Candidate turn is usually two or three calls (`get_application`, maybe
  `record_answer`, then the reply), and each call carries the whole session so far.
- **Tokens are estimated, not counted:** characters / 4 over message text, tool-call names
  and arguments, and the tool schemas as JSON. No tokenizer is available offline in this
  repo (tiktoken is not a dependency, and Claude's tokenizer is not public). Spanish prose
  usually runs a little above 4 characters per token and JSON a little below, so treat the
  numbers as plus or minus 15 %.
- **Scope:** every seed persona (12) plus the Approval -> slots -> booking scenario, played
  one at a time by the driver against the stack; 286 model calls.
- Rerun: `CRIBA_TURN_SIZE_JSON=<file>` with the scenario suite writes the summary below.

## Numbers (lane W3-1, before spec 006)

| | Estimated input tokens |
|---|---|
| p50 per model call | 6,177 |
| p90 per model call | 7,521 |
| max per model call | 8,464 (`invalid_then_corrected_es`, 82 messages in the session) |

Median split of one call, in characters (tokens at /4):

| Part | Characters | Tokens (est.) |
|---|---|---|
| System prompt (Hermes's own prompt + `SOUL.md` + Screener skill) | 15,757 | ~3,940 |
| Tool schemas (8 criba tools) | 4,810 | ~1,200 |
| Conversation so far (history and tool results) | 4,137 | ~1,030 |

## After spec 006b (structured answers, lane 006b)

Same method, 13 personas plus the Approval -> booking scenario; 339 model calls.

| | W3-1 | after 006a (schemas only) | after 006b |
|---|---|---|---|
| p50 per model call | 6,177 | | 7,278 |
| p90 per model call | 7,521 | | 8,767 |
| max per model call | 8,464 | about 9,084 | **9,680** (`invalid_then_corrected_es`, 84 messages) |

The longest call splits into 18,226 characters of system prompt (about 4,557 tokens), 6,129 of
tool schemas (1,533) and 14,365 of history (3,592). What 006b added to it:

- **The Screener skill: +1,972 characters, about 493 tokens**, against the budget of 600. The
  first draft was 2,383 (596 tokens) and put the longest call at 9,783, over the 9,700 target.
  Trimmed: the examples (one or two per field instead of three), the attempt 1-3 bullet, and the
  es-MX opening template, folded into the es-ES one as a one-line difference.
- **History, about +100 tokens in the longest session**: `get_application` now carries
  `{"language": "es-ES"}` on every typed message (about 20 characters a call over roughly 20
  calls). A `search_zones` round trip is about 300 tokens (a call of about 25 tokens plus a result
  of up to 10 rows), paid only by the turns that name a Zone; the longest session names it once.
- The tool schemas were counted in 006a (`search_zones`, `language`, the `value` definitions).

Headroom: 320 tokens to the 10,000 limit, 20 to the 9,700 target. It is thin on purpose, so
read it this way: **a session longer than `invalid_then_corrected_es` crosses the target**, and
a few more re-asks cross the limit. Spec 006a task 11 gives some back: dropping the legacy
`string` and `array` branches of `record_answer.value` shortens the schema on every call. Rerun
with real usage in W3-3: this is still an estimate (characters / 4).

## After the 006a clean-up (tasks 11-12)

Same method and scenarios (13 personas plus the Approval -> booking scenario); 339 model calls.

| | after 006b | after the clean-up |
|---|---|---|
| p50 per model call | 7,278 | 7,239 |
| p90 per model call | 8,767 | 8,732 |
| max per model call | 9,680 | **9,640** (`invalid_then_corrected_es`, 84 messages) |

Only the tool schemas changed: 6,129 characters before, 5,967 after (162 fewer, about 40
tokens on every call). The legacy `string` and `array` forms of `record_answer.value` and their
"Legacy" note are gone; the tool description gained a few words saying a tapped id goes in a
list for list fields and the Zone. The system prompt (18,226 characters) and the longest
history (14,365) are unchanged. Headroom: 360 tokens to the 10,000 limit, 60 to the 9,700
target.

## What dominates (W3-1 numbers)

The fixed part, about **5,140 tokens**, is paid on every call: the system prompt is three
quarters of it, the tool schemas the rest. The conversation adds roughly 100 to 160 tokens
per model call as the session grows (each `get_application` result is about 500 characters
and stays in the history). The largest call is the longest session: three invalid name
attempts, an invalid start date and a correction at the confirm step.

Headroom against 10,000 is about 1,500 tokens on the worst seed persona. A session about
twice as long as `invalid_then_corrected_es` (many re-asks, a Reopen, then the Interview
booking) would cross it. Levers, in order of size: trim the Screener skill and Hermes's
default prompt text; shorten tool descriptions in `contracts/tools.json`; have
`get_application` return only what the next step needs.

## Changed with this measurement

- A Candidate message is cut to 2,000 characters before it reaches the agent
  (`criba.channel.router.MAX_RUN_INPUT_CHARS`; it is stored whole). A 20,000-character
  message, which the web chat contract allows, would otherwise add about 5,000 tokens to
  that call and to every later call of the session, past the budget on its own.

## Caveat

An earlier real-model run (Q39 proof) reported about 9,800 input tokens per turn from
OpenRouter's usage, against about 6,200 estimated here. The two are not the same quantity
(real tokenizer against characters / 4, and possibly per run rather than per call), so
check the budget again with real usage in the W3-3 model comparison run.

## Real usage

From OpenRouter's generation records (`native_tokens_prompt`, the real tokenizer, one per model
call), in the two real-model comparison runs of 2026-10-10 (`model-comparison.md`). Before is
`evidence/real-model-2`, partial (Haiku 7 personas with the `summary` step, Sonnet 1). After is
`evidence/real-model-3`, after `fix/turn-size-real`: all 9 usable personas on each model, plus
the `summary` step on Haiku.

| | Before: calls | p50 | max | Over 10,000 | After: calls | p50 | p90 | max | Over 10,000 |
|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | 116 | 10,905 | 13,316 (`language_switch_mx`) | 85 (73%) | 248 | **8,182** | 11,312 | 12,793 (`zone_ambiguous_mx`) | 52 (21%) |
| Sonnet 5.5 | 24 | 12,688 | 16,113 (`confirm_correction_mx`) | 21 | 204 | 8,392 | 10,473 | 12,504 (`invalid_then_corrected_es`) | 30 (15%) |

The first call of a session is now 6,555 to 6,588 real tokens on both models, near the 6,399
the calibrated estimate predicted ("Headroom and what is still an estimate", below).

**The 10,000 limit applies to screening turns, which run on Haiku, and it is not met on every
call.** All 52 Haiku calls over it are in the two Zone runs that went wrong: 47 in
`zone_ambiguous_mx`, where Haiku did not record a tapped Zone and the driver kept tapping until
the 60-turn limit (61 Candidate turns against 7 to 14 for a normal screening), and 5 in
`zone_freeform_mx` (max 10,872, eight Candidate turns that ended in an Escalation). In the other
seven personas and the `summary` step every Haiku call is under 10,000, max 9,625
(`confirm_correction_mx`), which is within the 9,500 target plus the 5% margin the target was
set for. A session that runs long (repeated re-asks after an Escalation) still grows past the
limit, because nothing caps the history. Sonnet writes longer replies and re-asks more: its 30
calls over the limit are in five personas (max 12,504). About 98% of the prompt was read from
the cache on both models.

### After round 2 (branch `fix/turn-size-round2`, 2026-10-10)

Same measure, taken from the `in=` figure Hermes logs for every model call
(`agent.conversation_loop: API call #n ... in=`, the prompt tokens OpenRouter reports, cache
included), on a real Haiku 5.5 stack (`criba-trim2-real`, port 18116) with the history
middleware of "Round 2" below. The four runs the brief asked for:

| Persona | Runs | Calls | Max real input tokens per call | Over 10,000 |
|---|---|---|---|---|
| `zone_freeform_mx` | 2 | 27 and 26 | **9,441** and **9,596** | 0 |
| `qualified_es` | 1 | 17 | 9,058 | 0 |
| `zone_ambiguous_mx` | 1 | 23 | 9,287 (outcome as expected) | 0 |
| Total | 4 | 93 | 9,596 | **0** |

Before the middleware, the same persona on the same stack, today: `zone_freeform_mx` 10,821
(5 of 26 calls over 10,000); in the round-1 run 11,066 (5 to 8 calls over).

More runs on the final code, inside the spend cap: `invalid_then_corrected_es` (15 turns, 35
calls) 9,669; `language_switch_mx` 8,916; `multi_answer_es` four runs 9,997, 9,028, 9,060 and
9,231 (the 9,997 is a run in which Haiku searched the Zones of Madrid again in its last turn,
which adds a ten-row list to the turn); `confirm_correction_mx` five runs 7,664 (stalled at the
Zone after three turns), 8,966, 8,757, 8,882 and 9,636. All 329 calls on the final code, over
seven personas: **none over 10,000**, max 9,997. Without the middleware, `confirm_correction_mx`
four runs: 10,128, 10,146, 10,972 and 9,482 (6 of 90 calls over). Spend by the guard's prices:
$0.2130 for the 25 real runs of the lane, baselines and experiments included ($1.6921 to
$1.9051 of the $2.30 stop; the ledger is `/tmp/criba-real-spend.json`, cap $5).

The headroom is thin on the longest sessions: **about 400 tokens** under the limit at the
worst call, and a session of three more turns than these would cross it. The first call of a
session is unchanged (6,660 to 6,673), so about 6,700 of every call is the fixed prompt.

## After the real-usage fix (branch `fix/turn-size-real`)

Same sim stack and the same 13 personas plus the Approval -> booking scenario (339 model
calls), no real-model call. The limit is still **10,000 real input tokens** per call; the
estimate is now calibrated (next section) and the target for the longest call is **9,500**.

### Calibration: real tokens per estimated token

The old estimate (characters / 4) read **1.5 times too low**. The only real counts are the
OpenRouter records of the comparison run (`model-comparison.md`), and the stub run of the same
prompt gives the estimate for the same persona:

| | Real (OpenRouter) | Old estimate (stub, same prompt) | Ratio |
|---|---|---|---|
| Haiku 5.5, `language_switch_mx`, longest call | 13,316 | 8,712 | 1.53 |
| Haiku 5.5, p50 over 116 calls (7 personas) | 10,905 | 7,227 (all 13 personas) | 1.51 |
| Sonnet 5.5, `confirm_correction_mx`, longest call | 16,113 | 9,044 | 1.78 |

`REAL_PER_ESTIMATED = 1.5` in `src/tests/sim/test_scenarios.py` is the Haiku fit (Haiku is the
production model); the number the test reports and asserts is `ceil(chars / 4 * 1.5)`. Limits:

- No tokenizer is available offline (Claude's is not public, tiktoken is not a dependency), so
  this is one ratio for prose, JSON and Spanish alike.
- The real runs also wrote longer replies and re-asked more than the scripted stub does, which
  the 5% between the 9,500 target and the 10,000 limit is for. The ratio above folds some of
  that in, so it is on the high side as a pure tokenizer ratio.
- The one Sonnet data point is 1.78, about 1.15 times Haiku's. The 10,000 limit is met for
  Haiku; Sonnet would need its own cut or a ratio of its own.

Seen with this ratio, the old numbers were all over the limit: the longest call of the suite
was 9,640 estimated, **14,460 calibrated**, and every one of the 13 personas peaked over 10,000.

### Where the tokens went (longest call, `invalid_then_corrected_es`, 84 messages)

| Part | Before (chars) | After (chars) | What changed |
|---|---|---|---|
| Hermes's own system text | 4,247 | 46 | see "What was cut" |
| `SOUL.md` | 1,415 | 1,309 | tighter wording |
| Screener skill (without frontmatter) | 12,566 | 11,019 | tighter wording, no rule dropped |
| Tool schemas (9 tools) | 5,967 | 4,683 | shorter descriptions, no size limits |
| History (messages and tool calls) | 14,365 | 7,919 | smaller tool results |
| **Total** | **38,560** | **24,977** | |
| Estimated tokens (chars / 4) | 9,640 | 6,245 | |
| **Calibrated tokens (x 1.5)** | **14,460** | **9,367** | limit 10,000, target 9,500 |

The fixed part (system prompt and tool schemas, paid on every call) went from 24,195 to 17,058
characters; the first call of a session from 6,050 to 4,266 estimated tokens (9,075 to 6,399
calibrated).

Per persona, longest call:

| Persona | Calls | Before, estimated | Before, calibrated | After, estimated | After, calibrated |
|---|---|---|---|---|---|
| `confirm_correction_mx` | 34 | 9,044 | 13,566 | 5,889 | 8,834 |
| `invalid_then_corrected_es` | 42 | 9,640 | 14,460 | 6,245 | 9,368 |
| `language_switch_mx` | 30 | 8,712 | 13,068 | 5,608 | 8,412 |
| `multi_answer_es` | 26 | 8,345 | 12,518 | 5,431 | 8,147 |
| `no_license_mx` | 18 | 7,655 | 11,483 | 5,239 | 7,859 |
| `opted_out_code_switch_es` | 14 | 7,166 | 10,749 | 4,814 | 7,221 |
| `prompt_injection_es` | 31 | 8,773 | 13,160 | 5,636 | 8,454 |
| `qualified_es` | 24 | 8,219 | 12,329 | 5,382 | 8,073 |
| `stalled_abandoned_es` | 9 | 6,777 | 10,166 | 4,621 | 6,932 |
| `withdrawn_mx` | 20 | 7,748 | 11,622 | 5,079 | 7,619 |
| `zone_ambiguous_mx` | 33 | 9,023 | 13,535 | 5,794 | 8,691 |
| `zone_freeform_mx` | 36 | 9,582 | 14,373 | 6,214 | 9,321 |
| `zone_outside_es` | 16 | 7,434 | 11,151 | 5,125 | 7,688 |

Across the suite (339 calls, calibrated): p50 **7,305** (was 10,859), p90 **8,442**, max
**9,367** (was 14,460).

### What was cut, cheapest first

1. **Hermes's own system text, 4,247 to 46 characters (about -1,050 estimated tokens).** The
   prompt of `nousresearch/hermes-agent:v0.21.6` is built by `build_system_prompt_parts`
   (`agent/system_prompt.py:734-798`); after the identity and the skill it appends these blocks:
   - "Finishing the job" (769 characters, `agent/prompt_builder.py:383`) and "Parallel tool
     calls" (618, `prompt_builder.py:420`), added in `_guidance_parts`
     (`system_prompt.py:557-561`). Two config keys, `agent.task_completion_guidance` and
     `agent.parallel_tool_call_guidance` (read at `agent/agent_init.py:1417-1422`), are false in
     both profiles.
   - The api_server platform hint (712, `prompt_builder.py:832`), replaced through
     `platform_hints.api_server.replace` (`agent_init.py:1431-1432`, `system_prompt.py:55-68`)
     by "Plain text only: no markdown, no MEDIA: tags." (45).
   - Five blocks with **no config key**: the pointer to the Hermes docs (466,
     `prompt_builder.py:183`), the `/steer` briefing (771, `STEER_CHANNEL_NOTE`,
     `prompt_builder.py:604`; the Runs API never delivers a steer), the active-profile
     paragraph (396, `system_prompt.py:373`; it names `/opt/data/profiles/...` and the default
     profile's directories), the conversation-start line with model and provider (121,
     `system_prompt.py:489`), and the runtime-environment block (374,
     `prompt_builder.py:1143`; host, home, working and scratch directory, and spike 1 found a
     host path in a reply). The `criba-tools` plugin now registers `llm_request` middleware
     (`hermes_cli/middleware.py:88`, `hermes_cli/plugins_dispatch.py:593`) that removes them
     from the system message just before the request goes out
     (`src/hermes/plugins/criba_tools/prompt_trim.py`). It handles a string system message and
     cache-marked text blocks, and fails open: an unrecognised block stays, a raising callback
     is skipped. The scenario suite fails if any of the blocks reaches the stub model, so a
     Hermes upgrade that rewords one is caught there.
2. **The skill, 12,566 to 11,019 characters (about -390 tokens), and `SOUL.md`, 1,415 to
   1,309.** Removed what the rest of the prompt already said (the skill's intro repeats
   SOUL.md, a placeholder preface, the Need-a-moment and Interview-offer templates, most of the
   English opening, an ADR number, parenthetical examples). Every rule stays, and the exact
   phrases the profile tests lock; the skill was tight already, so this is the smallest cut.
   One line was added: a missing `get_application` key is null or false, and `answers` arrives
   only when `next_field` is null.
3. **Tool schemas, 5,967 to 4,683 characters (about -320 tokens).** Eight descriptions are
   shorter, with the full text kept in the contract's `$comment` (which the plugin strips):
   2,229 to 1,495 characters. The plugin also stops sending size and format limits
   (`minLength`, `maxLength`, `minItems`, `maxItems`, `uniqueItems`, `minimum`, `format`,
   `default`; about 350 characters): the backend checks each again with its own message and the
   skill names the shapes. Types, enums, the zone id `pattern` and `additionalProperties: false`
   stay.
4. **Tool results in the history, 14,365 to 7,919 characters (about -1,600 tokens).** A result
   stays in the context for the rest of the session. The longest session carried 14
   `get_application` results of 525 to 609 characters and 9 accepted `record_answer` results of
   293 to 384, mostly null placeholders and a repeated `answers` object. Now (a contract
   change: `contracts/tools.json` and its byte-identical copy, `check_contracts.sh` passes):
   - `get_application` omits `outcome`, `knockout_criterion`, `first_name`, `invalid_attempts`,
     `reopened`, `takeover_summary`, `tone_hint`, `approved` and `interview` when they are
     null, false or 0, and sends `answers` only when `next_field` is null (the confirm step
     needs the summary; while screening, `next_field` is all the model needs). The 14 results
     go from 525-609 characters (average 575) to 175-452 (average 234).
   - An accepted `record_answer` sends `answers` only when `next_field` is null. The 9 results
     go from 293-384 characters (average 364) to 107-374 (average 182).
   - `search_zones` rows send `country` only when it is not the Application's own and
     `covered` only when it is false: the nine-row CDMX list goes from 1,038 to 783 characters.
   - The stub model gets a `tool_json_absent` match for the one rule that read
     `interview: null`.
5. **History compaction, not used.** Hermes can prune old tool results
   (`compression.proactive_prune_tokens`, `agent/context_compressor.py:3482`), but the pass only
   summarises results above `proactive_prune_min_result_chars` (default 8,000, floor 200)
   outside a protected tail of `protect_last_n` (20) messages, rewrites the stored session
   (`archive_and_compact`) and breaks the prompt-cache prefix on every commit. After cut 4 the
   results are small, and the cache hit (about 98% of the prompt) is worth more than the few
   hundred tokens a prune would reclaim. Left at its default, off.

### Tests

- `test_scenarios.py` asserts that the calibrated estimate of every call is at most 10,000 and
  of the longest call across the suite at most 9,500 (`TURN_TOKEN_TARGET`), reports both the
  plain and the calibrated figure, and fails if Hermes boilerplate reaches the model.
- `test_profiles.py`: SOUL.md plus the skill under 13,200 characters (was 14,500); the two
  guidance keys and the platform hint are set. `test_plugin_schemas.py`: the schemas sent to
  the model are at most 5,000 characters and carry no `$comment` and none of the stripped
  limits. `test_prompt_trim.py`: the middleware on a string system message, on cache-marked
  blocks, on a top-level `system`, and on input it must ignore.
- `tests/tools`: the results carry no empty keys and no early `answers`. The old assertions
  read `get_application` through a view that defaults the missing keys, as the skill tells the
  model to, so they keep their meaning.

### Headroom and what is still an estimate

The longest persona is at 9,367 calibrated: **133 under the 9,500 target** and 633 under the
limit. That is thin, as it was before: a session with more re-asks than
`invalid_then_corrected_es`, or replies longer than the stub's, eats into it. Nothing here is a
real count. The check that settles it is the capped real-model re-run: read
`native_tokens_prompt` per call again and compare it with the table above (the first call of a
session should now be near 6,400 real tokens).

## Round 2: what finished turns left in the history (branch `fix/turn-size-round2`)

After round 1 every persona stayed under 10,000 real tokens on Haiku except `zone_freeform_mx`
(12 turns, a nine-row CDMX list from `search_zones`, the "¿Vives en Toluca?" confirmation): 5 to
8 calls over, max 11,066. The estimate here is the one calibrated in the previous section
(chars / 4 times 1.5).

### The breakdown of its longest call (stub stack, project `criba-trim2`, 68 messages)

| Part | Characters before | After | Note |
|---|---|---|---|
| System prompt (`SOUL.md` + skill) | 12,688 | 12,688 | unchanged |
| Tool schemas | 4,683 | 4,683 | unchanged |
| History: tool results | 5,305 (22) | 1,501 | 11 `get_application` 2,472 to 843; 9 `record_answer` 2,009 to 595; `search_zones` 783 to 22 |
| History: the model's tool calls | 1,167 | 1,167 | kept as the model made them |
| History: assistant text, Candidate text, `[tap result]` | 707 + 188 + 135 | 707 + 188 + 69 | |
| **Total** | **24,873** | **21,003** | |
| Estimated tokens (chars / 4) | 6,219 | 5,251 | |
| **Calibrated tokens (x 1.5)** | **9,328** | **7,877** | target 9,500 |

Tool results were 5,305 of the 7,502 history characters, and the history 30% of the call. On the
real model the history costs more than the stub says, and the prompt carries something the stub
never sends:

- A fit of the 145 real calls of this lane, with the history rebuilt from Hermes's session
  store: about **0.72 real tokens per character of history** (intercept 6,800, the fixed
  prompt), against 0.375 in the calibration (1.5 / 4). Messages are short, JSON-heavy and each
  carries tool-call framing that chars / 4 does not see. The stub's script also differs from
  the real model's session (Haiku skips most of the `get_application` calls the stub makes, but
  writes longer replies, searches again, and carries the reasoning of the turn in progress). So
  the 9,500 target on the calibrated stub figure is not a bound on real counts: the same persona
  is 7,877 calibrated and 9,441 to 9,596 real. The real run is the arbiter.
- **`reasoning_details`.** Haiku 5.5 returns the signed record of its hidden reasoning on a
  typical assistant message (about 1,400 characters of signature, 8 messages in a 12-turn
  session in Hermes's `state.db`), and Hermes replays it to OpenRouter on every later call
  (`agent/transports/chat_completions.py`, `_route_replays_reasoning_details`: "a route that
  genuinely replays it (OpenRouter, #129037) still receives it").

### What was cut

One change, in the criba-tools plugin (ADR 0045, `src/hermes/plugins/criba_tools/history_trim.py`),
registered as a second `llm_request` middleware next to the system-prompt trim
(`hermes_cli/middleware.py:88`, `hermes_cli/plugins_dispatch.py:593`; it runs after the transport
has built the messages, `agent/turn_api_request.py:143`). On the copy of each request, for every
message before the latest user message:

1. **A tool result over 90 characters becomes its outcome**: `ok`, `error`, `reason`, `attempt`,
   `value.status` (else `stage`), `value.field`, and `options`/`confirm` when the options are
   short (at most 200 characters). `[tap result]` user messages get the same. The ten-row Zone
   list and the `ambiguous_zone` error that repeats it become `{"ok":true,"value":{}}` and
   `{"ok":false,"error":"invalid_value","reason":"ambiguous_zone"}`.
2. **An assistant message loses `reasoning_details`, `reasoning_content` and `reasoning`.** Its
   text and tool calls stay.

The turn in progress is untouched (the provider needs its reasoning back with its tool calls),
the stored session is untouched, and the rewrite is a pure function of the messages, so a message
is rewritten the same way on every later call and the cache prefix holds from the previous turn
back. It fails open on any shape it does not know.

**Hermes's own pruning was looked at again and not used** for the reason in round 1, cut 5: it
rewrites the stored session and breaks the cache prefix on each commit
(`agent/context_compressor.py:3482`).

**Not done, and why:**
- *Shorter `search_zones` rows and a minimal `[tap result]`* (contract changes): a result is read
  once, in its own turn, and the middleware now takes it out of every later call. Cutting the
  city from a ten-row list saves about 150 characters (about 100 real tokens) in that one turn.
  `contracts/tools.json` is unchanged, so `check_contracts.sh` has nothing new to prove.
- *Dropping the finished turns' `get_application` call and result pairs*: the model would stop
  seeing itself call it first each turn.
- *Reasoning off for Haiku*: a behaviour change on the model that screens Candidates.

### Which of the two does the work (real Haiku, `zone_freeform_mx`, max per run)

| Middleware | Runs | Max real tokens | Calls over 10,000 |
|---|---|---|---|
| neither | 1 | 10,821 | 5 of 26 |
| tool results only | 2 | 10,139 and 11,036 | 3 of 32 and 5 of 26 |
| reasoning only | 2 | 10,739 and 10,831 | 5 of 26 and 5 of 25 |
| **both** | 3 | **9,441, 9,475 and 9,596** | 0 of 79 |

Neither half is enough alone; together they take about 1,300 tokens off the end of the session.
Cells of one or two runs show the size of the effect, not its variance (the spend cap stopped
the table here). The three `both` runs are the two final runs and the run of the first version
of the middleware, which did not yet keep short option lists.

Behaviour is unchanged where it could be compared: `zone_freeform_mx` and `qualified_es` fail
the same persona checks as before (`answer_invalid` expected and not produced, the licence asked
before the Zone), `confirm_correction_mx` stops at stage `confirm` in 3 of 4 runs with the
middleware and 3 of 4 without, and `zone_ambiguous_mx`, `invalid_then_corrected_es`,
`language_switch_mx` and `multi_answer_es` end as the persona expects.

### Stub numbers (13 personas and the Approval scenario, 331 calls, calibrated)

| | Round 1 | Round 2 |
|---|---|---|
| p50 per call | 7,305 | 7,068 |
| p90 | 8,442 | 7,587 |
| max | 9,367 (`invalid_then_corrected_es`) | **8,070** (`invalid_then_corrected_es`) |
| `zone_freeform_mx`, longest | 9,321 | **7,877** |

Per persona, longest call: `approval_booking` 7,966, `confirm_correction_mx` 7,842,
`invalid_then_corrected_es` 8,070, `language_switch_mx` 7,672, `multi_answer_es` 7,526,
`no_license_mx` 7,360, `opted_out_code_switch_es` 7,035, `prompt_injection_es` 7,684,
`qualified_es` 7,500, `stalled_abandoned_es` 6,927, `withdrawn_mx` 7,253, `zone_ambiguous_mx`
7,787, `zone_freeform_mx` 7,877, `zone_outside_es` 7,320.

### Tests

- `test_scenarios.py`: `test_turn_size_report` asserts that the longest call of
  `zone_freeform_mx` alone is at most 9,500 (`LONGEST_FLOW_PERSONA`; the test plays it when it
  runs by itself), next to the existing 9,500 on the longest call of the suite and 10,000 on every
  call; it reports `max_tokens_by_persona`. `_history_leaks` fails any scenario in which a tool
  result of 300 characters or more from a finished turn reached the stub.
- `tests/hermes/test_history_trim.py` (shapes of the compaction, the turn in progress, reasoning
  keys, cache-marked blocks, idempotence, garbage in) and `test_prompt_trim.py` (both
  middleware are registered).
