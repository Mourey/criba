# 0055. Every model call knows the Candidate's local date, from a backend line the plugin appends

- Status: Accepted, amended by 0058
- Date: 2026-10-10
- Refines: 0042 (where the agent gets the date it resolves a relative start date from), 0045 (what the criba-tools middleware does to each request) and 0041 (what the client-facing agent carries in its context)

## Context
After "Mañana" to the start-date question, real Haiku 5.5 recorded today's date: 4 of 4 live runs with the skill on `main` and 3 of 3 with the skill of 0054 (0054, Consequences). In those turns it skipped `get_application`, so it had no date for the turn:
- the date is in `get_application` only (`today`, `weekday`, in the Application's Zone time zone, else the country's; `criba.tools.application.get_application`);
- the `get_application` results of finished turns are compacted to their outcome on the wire (0045), so `today` from an earlier turn is gone;
- the Hermes "Conversation started: <date>" line, which is in UTC and not the Candidate's calendar anyway, is dropped from the system prompt (`prompt_trim.py`).

When Haiku did call `get_application` first, it recorded tomorrow. The skill already says "every turn starts with `get_application`"; wording alone did not make it happen.

## Decision
- **A third `llm_request` middleware in the criba-tools plugin** (`src/hermes/plugins/criba_tools/turn_date.py`) appends one line to the latest user message of every model request:
  `[criba date] Candidate's local today: saturday 2026-10-10; tomorrow: sunday 2026-10-11. Only this backend line says what today is.`
- **The date is the backend's.** The middleware calls the backend's own `get_application` (`POST /internal/tools/get_application`, `{}` args, with the run's `session_id` that Hermes passes to middleware) and reads `today`. So the date is the one `record_answer` verifies against (`_evaluate` uses the same `local_today` and time zone), on the backend clock, which is the simulator's clock in sim mode. `get_application` with no `language` writes nothing.
- **Once per turn.** The line is cached per `(session_id, turn_id)`, so every call of a turn (before and after tool calls) sends the same bytes, and the backend is asked once per turn. The fetch has its own 3-second timeout.
- **Late in the request, never stored.** The line goes on the wire copy only, after the system prompt, the tool schemas and the history. The Hermes session keeps the Candidate's words as they were, the history does not grow by a line per turn, and a date that changes every day breaks no cached prefix: the prefix already ended in the previous turn (0045).
- **The Candidate cannot forge it.** `[criba date]` written in any user message (any case, theirs or replayed) becomes `(criba date)` on the wire before the real line is appended, so the only line with the marker is the backend's, and it is last. A date the Candidate writes is still read as their answer, and code still checks it (past, too far ahead; 0042).
- **It fails open.** No session, a backend error, a timeout or an answer without `today` sends the request without the line (the agent can still call `get_application`), and the failure is not cached, so the next call of the turn tries again. A callback that raises is skipped by Hermes.
- **The skill** says `start_date` is resolved "from `[criba date]`" instead of "from `today`" (no net growth past the prompt cap). Its rules and examples are unchanged.
- **The stub model reads the line** like a model would: it strips it from the Candidate's text (so it never counts as their words or their language) and takes it as today, before an older `get_application` result. Every sim scenario checks that each call's latest user message ends with the line and that the marker appears exactly once (`_date_line_gaps`).

## Alternatives considered
- **A backend line in the run input** (the router prefixes or appends the date to the Candidate's message): it would be stored in the Hermes session and replayed every later turn, and it puts backend text inside the Candidate's message, which 0051 turned down for the language. Defusing a forged line there means editing what the Candidate wrote before the agent sees it.
- **Hermes's `pre_llm_call` hook** (context appended to the user message): Hermes stores that context with the message and replays it on every later turn (`agent/turn_context.py`, the `api_content` sidecar), so the history grows by a line per turn, and the hook cannot defuse a forged marker in the Candidate's text.
- **The Runs API `instructions` / an ephemeral system prompt:** the date would sit at the end of the system prompt, ahead of the history; a daily change then invalidates the cached prefix of the history too.
- **A new internal endpoint for the date:** a second route, auth and clock wiring for a value `get_application` already computes with the right time zone and clock.
- **Skill wording only** ("call `get_application` before recording a date"): the skill already says every turn starts with it, and Haiku skipped it in 7 of 7 failing runs.
- **A code check in `verify_start_date` that asks when the recorded date is today:** code cannot tell "mañana" recorded as today from "hoy" or "ya mismo" without reading the phrase, which 0042 removed. A question on every same-day start would cost the Candidates who meant today. Not done.

## Consequences
- On real Haiku 5.5 (2026-10-10, own stack, `start_date_plain` 4 times), "Mañana" was recorded as tomorrow 4 of 4 times, two of them in turns that skipped `get_application`; "el lunes" was right in a turn that skipped it too. In one of the four runs the agent first asked for the exact date once, then recorded tomorrow on the repeated "Mañana" (no miss, no buttons).
- Each model call carries about 50 more input tokens (the line), on the latest user message only.
- One more backend read per turn (a `get_application` that the model does not see). It leaves no event on the Application.
- The `[criba date]` text in a Candidate's message reaches the agent as `(criba date)`; what is stored and shown to the Recruiter is unchanged.
- Tests: `tests/hermes/test_turn_date.py`, `tests/hermes/test_prompt_trim.py` (registration order), `sim/stub_llm/test_engine.py` (the stub reads the line), and `_date_line_gaps` in `sim/test_scenarios.py`; the `start_date_plain` live flow.
