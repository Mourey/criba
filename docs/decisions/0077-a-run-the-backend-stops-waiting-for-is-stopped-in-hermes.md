# 0077. A run the backend stops waiting for is stopped in Hermes, and its ledger row says when its usage is unknown

- Status: Accepted
- Date: 2026-10-14
- Refines: 0018 (the Runs API), 0019 (one usage row per run), 0073 (a timed-out turn is retried under the same key)

## Context
The run client stops waiting for a run at `CRIBA_RUN_TIMEOUT_S` (60 s), and the router sends the holding message. Until now nothing told Hermes. The run could go on spending tokens and calling tools after the Candidate had been told to wait, and the usage row of the timeout held zero tokens, so the ledger undercounted (code review prep, `TODO.md`).

What the pinned Hermes (`nousresearch/hermes-agent:v0.21.6`, by digest) offers, read from the image's `/opt/hermes/gateway/platforms/`:

- **A stop route exists.** `POST /v1/runs/{run_id}/stop` is registered next to the other run routes (`api_server_runs.py:234-240`), also under `/p/{profile}` (`api_server.py:4488-4489`), and advertised as `run_stop` (`api_server.py:69`, `:88`). The handler (`api_server_runs.py:1253-1274`) answers a run already over with its status as it stands, usage included for a completed run (`:1262-1263`); a run not live in the process gets `409 run_not_active` (`:1264-1266`); otherwise it marks the run `stopping`, asks the agent for a hard interrupt and reaps that run's background processes, and answers `{"run_id", "status": "stopping"}` (`:1267-1274`). The stop is cooperative: the executor thread keeps going until the agent notices (`api_server.py:1342`).
- **A stopped run reports no usage.** An interrupted turn ends `cancelled` (`api_server_runs.py:183-191`), and both cancelled endings publish the status without `usage` or `runtime` (`:973-974`, `:987-988`). Only a completed run carries them (`:978-986`). A failed one carries neither (`:975-977`).
- **The agent can still finish.** If the turn completes before the agent notices the interrupt, the result is not `interrupted` and the run ends `completed`, with usage (`:185-191`, `:966-986`).
- Sessions (`GET /api/sessions/{id}`) expose cumulative per-session token counts (`api_server.py:2985-2992`). Whether they include an interrupted turn's calls was not verified, and a per-run figure would need a before-and-after read; not used.

The frozen ledger schema has no way to say "unknown": the token columns are `NOT NULL DEFAULT 0` and `status` is `ok`, `error` or `timeout` (`contracts/schema.sql`, `usage`).

## Decision
- **On a timeout the run client stops the run.** Both the poll and the events stream, when they reach the deadline with a `run_id`, `POST {prefix}/{run_id}/stop` with the profile's key and a 2 s timeout of its own (`STOP_TIMEOUT_S`). Best effort: any error, timeout or non-200 answer is swallowed and named in the failure message (`stop failed: …`); the caller gets the same timeout failure either way. A run that completes at or before the last poll is never stopped, and nothing else (a success, a refusal such as a 429, a failed run, an unreachable Hermes) calls the route.
- **Usage the stop answer carries is kept.** When the run was already over at the stop, its answer holds the run's usage and runtime; they ride on the timeout failure and land on the ledger row (model, provider, tokens, estimate).
- **The Idempotency-Key and session behaviour do not change.** The timeout stays not `final` unless the stop answer reports the run failed: the owed-turn retry (0073) re-POSTs the same key, and Hermes replays the run whatever it became. A run that finished despite the stop is delivered from the replay, and its success replaces the timeout row. A run the stop ended is replayed as `cancelled`, which is final, so the next attempt takes a new key.
- **The ledger marks unknown usage.** A failure of a run Hermes took (it has a `run_id`) that reports no usage gets an error starting with `[usage unknown]`; its zero token counts mean "not reported", not "nothing spent". A failure that never reached Hermes (no `run_id`) keeps plain zero counts. A row that already exists keeps its mark as it was, since its counts are not overwritten by a failure; a later success clears it with the real counts.

## Alternatives considered
- **Keep waiting for a slow run so a retry can pick up its answer (0073 as written).** Cheaper for the Candidate when the run does finish, but a run past 60 s keeps spending and may call tools after the holding message, which is what this fixes.
- **Poll after the stop until the run is terminal.** It would mark a cancelled run final at once and save one retry attempt, but adds up to the agent's reaction time to every timeout, and the stop is cooperative with no bound.
- **Read the session's cumulative counts before and after the run.** Could estimate a stopped run's spend, but two extra calls per run, concurrent runs on the session, and unverified counting of interrupted turns.
- **Make the token columns nullable, or add a usage-known column.** The honest model, but a change to the frozen `contracts/schema.sql` owned by a lane, and to the economics API that would show it. Left as a follow-up.

## Consequences
- After a timeout the run stops at the agent's next interrupt check: no more tool calls or model calls from that turn once it notices.
- A timed-out turn whose run the stop ended costs one extra retry attempt (the replay reports it `cancelled`), so its reply comes at the third attempt rather than the second.
- Known gaps: Hermes v0.21.6 reports no usage for a stopped run, so its spend stays out of the estimate; only the error mark and OpenRouter reconciliation (0019) account for it. The mark lives in the error text, which the economics views do not show. The stop does not cover a run whose POST answer never came back (no `run_id`), nor a failed run, which Hermes also reports without usage.
