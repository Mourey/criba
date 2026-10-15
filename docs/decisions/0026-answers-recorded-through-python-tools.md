# 0026. Answers are recorded through Python-backed tools; code validates and decides Knockouts

- Status: Accepted. Spike 2 (2026-10-09):
  - The tools are a **thin Hermes plugin that proxies to the backend**, using the dispatcher's `session_id`.
  - **MCP is rejected:** it has no per-session identity, and calls are serialised per server, so one slow call blocks every Candidate.
  - `application_id` must never appear in a tool schema, because the model echoes injected IDs.
  - Limiting the toolset cut a turn from about 40.6K to 7.2K input tokens.
  - See spike 2 (tool exposure).
- Date: 2026-10-09

## Context
Phase 2 requires structured JSON with every screening field, graceful rejection of invalid input, and the full conversation history. ADR 0016's legal basis for automated Knockouts needs those decisions to be deterministic.

## Decision
- **The Python backend exposes tools to the Screening Agent**, through an MCP server or a Hermes plugin (decided in the first spike): `get_application()`, `record_answer(field, value)`, `request_review()`, `raise_escalation(reason)`, `finish_screening()`, and `record_backout(kind)` for Withdrawn, Opted out or Erasure request.
- **`record_answer` validates against the Phase 1 rules** (name, Zone against Service Areas, dates in local time, and so on). It returns either the value it accepted, or a structured error with a hint that the agent turns into a friendly re-ask.
- **Code decides Knockouts.** When `record_answer` sees a failed Knock-out Criterion, it ends the Application and returns the approved Knockout template to send.
- **The Application row is the structured JSON**, current after every turn.
- **Our database stores every message verbatim.** The Hermes session is the agent's working memory and may be compacted; it is not the record.

## Alternatives considered
- **Having the LLM extract fields from the transcript after each turn:** validation would be non-deterministic and harder to test, and a Knockout would rest on model judgement.

## Consequences
- Validators are plain Python functions with unit tests. The simulator's personas test them from end to end.
- The agent's prompt shrinks to conversation and tone. The rules live in code.
