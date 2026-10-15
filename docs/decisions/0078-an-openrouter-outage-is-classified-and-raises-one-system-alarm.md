# 0078. An OpenRouter outage is classified from the run's error text and raises one system alarm, which a run that succeeds clears

- Status: Accepted
- Date: 2026-10-14
- Refines: 0073 (the `technical_error` Escalation after the fourth attempt), 0051 (Jev shares the key)

## Context
An OpenRouter outage (402 credits, 429 rate limit, a model unavailable) reached nobody who could fix it: each Candidate got the holding message and, after the fourth failed attempt, one unclassified `technical_error` Escalation, and Jev failed silently on the same key (the deployment design, "Gap: no alarm for an OpenRouter outage").

The open check was whether Hermes passes the upstream status through. Read from the pinned image (`nousresearch/hermes-agent:v0.21.6`, by digest), under `/opt/hermes/`:

- **It does not, as a field.** A failed run's terminal status and `run.failed` event carry `completed`, `partial`, `interrupted` and `turn_exit_reason` plus an `error` string (`gateway/platforms/api_server_runs.py:181-198` builds them; the failure branch is `:975-977`). The agent result also holds `failure_reason` and `failure_retryable` (`agent/turn_recovery.py:1139-1142`), but nothing forwards them.
- **The status is in the text.** `error` is the agent's summary of the API error, redacted (`api_server_runs.py:977`, `api_server.py:900-903`): `HTTP <status_code>: <message>` when the error has a status (`agent/api_error_summary.py:71-73`), the JSON body's `error.message` first (`:196`), cut to 300 characters. A network failure becomes "Hermes can't reach the model provider…" (`:43-49,163-168`). A billing-classified failure uses the same summary (`agent/conversation_loop.py:609-624`).
- So the classifier reads text, not a status. That is a convention of v0.21.6 and not a documented interface: a Hermes upgrade can change it, and `test_classify_reads_the_error_text_hermes_returns` is the place to update. What OpenRouter itself puts in the body (`error.metadata`) is not forwarded, so `limit_source` is not available.

## Decision
- **Classify where the failure is recorded.** `classify(RunFailed)` (`criba/channel/outage.py`) returns `credits` (HTTP 402), `rate_limited` (429), `key_rejected` (401), `model_unavailable` (404, 5xx, "can't reach the model provider") or `other`. Only a run Hermes reported failed (`final`) is classified: Hermes refusing the POST (`run not accepted: HTTP 429`, its own concurrency cap), a timeout and an unreachable Hermes are `other`. The code goes on the `run_error` event as `cause` (a code, ADR 0035) and, when it is not `other`, in the `technical_error` Escalation's note. It is not a new column: the usage row's `error` keeps the text it always had, and `classify` reads it the same way.
- **One alarm per cause.** An alarm opens when classified failures reach 3 Applications within 5 minutes, or at the first `credits` or `key_rejected` failure (those cannot be transient). It stays one however many Applications fail. A run that gets an answer from the model clears the alarms the runs raised. Raising and clearing are logged once, at ERROR and INFO.
- **A scheduled check of the key.** An hourly job (the scheduler's tick, the Clock's time) calls `GET https://openrouter.ai/api/v1/key` with the existing key file. `limit_remaining` below `CRIBA_OPENROUTER_KEY_FLOOR_USD` (default 5, about two days of the measured spend) raises `credits`; a 401 raises `key_rejected`; a key with no limit raises nothing. An answer that says nothing about the key (a transport error, a 5xx, no field) keeps the state. It is off in sim and test and with no key. The key is read once and never logged. The key alarm and the run alarm of one cause are the same alarm, with both sources named.
- **Where the Operator reads it.** `GET /health/alarms` on the backend returns `{"alarms": [{cause, sources, since}]}`, for the deployment's monitor next to `/health` (the deployment design, alarm table). It is not under `/api`, so the web proxy does not publish it, and it is outside the frozen contracts.
- **Candidates keep their handling.** The holding message, the retries and the per-Application `technical_error` Escalation are unchanged; the alarm is in addition, and the Escalation's note names the shared cause so a Recruiter sees it is not theirs to fix.

## Alternatives considered
- **Suppress the per-Application Escalations while an alarm is open** (the proposal in the deployment design). It would remove the flood, but an exhausted owed turn gets no more automatic attempts (0073), so its Candidate would wait with no human told. Keeping the turn owed past four attempts while the alarm lasts would work but changes 0073's retry rule; left as a follow-up for the owner to decide.
- **A queue item for the alarm.** `queue_items.application_id` is `NOT NULL` and the Escalation reasons are a frozen CHECK (`contracts/schema.sql`): a system item needs a contract change, and a web banner needs an `api.openapi.yaml` route and regenerated types.
- **Persist the alarm.** Needs a table in the frozen schema. In memory, a restart drops the run alarm until new failures reopen it, and the key check reopens its own at the first tick.
- **Alarm on Jev failures.** The key check already covers a dead shared key, and Jev's other failures are timeouts that 0051 treats as normal.

## Consequences
- An outage is one ERROR log line and one entry on `/health/alarms` instead of only N Escalations; nothing pages anyone by itself: the monitor has to poll the endpoint or the log.
- `run_error` events gain `cause`; `test_run_failures.py` pins the key set and was updated.
- Known gaps: the alarm is not in the web app; Jev-only outages and the account balance (a management key, the deployment design) are not covered; the classes depend on Hermes's error text staying `HTTP <status>: …`.
