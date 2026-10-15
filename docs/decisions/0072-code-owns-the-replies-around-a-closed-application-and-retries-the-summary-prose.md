# 0072. Code owns the replies around a closed Application, and the summary prose is retried

- Status: Accepted
- Date: 2026-10-12
- Refines: 0033 (the first message after a Knockout), 0021 (a new Application after Withdrawn), 0044 (code guarantees what the model is told to do), 0025 and Q26 (the summary prose)

## Context
An error-handling audit for the case requirement "graceful failures" (lane C, owner-approved 2026-10-12) found four gaps. Two are design decisions:

1. **A Candidate writing after a closed Application got an answer that did not fit.** After a Knockout, 0033 sends the message to the agent on the closed Application. A "hola" there made the stub (and can make a model) try the failed field, get `invalid_value`, and answer "No me ha quedado claro", which tells the Candidate nothing about the closed Application. After Withdrawn, 0021 starts a new Application, and the Candidate got a fresh greeting and the first question with no word that a new Application had started.
2. **A summary whose prose failed stayed without it, invisibly.** The writer stored `prose = None` and recorded the failure in the ledger, nothing retried, and the web app hid the missing prose: a Recruiter could not tell "failed" from "never asked". An exception after the structured part was built (the transcript, the ledger rows) lost the whole summary.

The other two are fixes with no design choice: the Jev call's 1.2 s timeout applied to each httpx phase, so a reply could wait two or three times the budget, and one failing scheduler phase skipped the rest of that tick.

## Decision
- **The first message after a Knockout is answered by code** (the 0044 pattern). In `Router.store_reply`, on a typed turn where the Application is still a closed Knockout after the run, the Knockout happened before this turn, this is the Candidate's first message since it, the run requested no Review and left no choice pending: the reply is `knockout_closed(criterion, language, country)` from `criba.channel.copy` (es-ES, es-MX, en; SYNTHETIC until Grupo Sazón supplies its own). It says the Application is closed and why, that they can give the failed answer again (0033's Reopen, within the 30-day window), and that "revisión" asks a person to review it. An empty run sends the same note. The agent still runs, so its session holds the message, and a Review request or a valid new answer keeps the agent's reply. Later messages are the agent's (0033). Spoken turns are left to the agent: their text was already heard as it streamed.
- **A new Application after Withdrawn opens with a welcome back.** The first reply of an Application whose Candidate's previous Application ended Withdrawn starts with `welcome_back(language, country)` on its own line ("as you withdrew your last application, we start a new one"), then the agent's greeting and first question. Typed only, for the same reason. Other Outcomes are unchanged: an Abandoned one reopens (0021), a Rejection, or a Knockout or Abandoned past 30 days, starts a new Application with no line, as before.
- **No reapply cooldown.** The Phase 1 design defines none beyond the 30-day Reopen window, so the copy promises nothing else.
- **The summary prose is retried, durably, and its state is visible.** The structured part is stored as soon as it is built; nothing in the prose step (the transcript, the call, the ledger rows) can hold it back. When the prose could not be written (an error, a 5xx, or answers cut off or carrying internal codes after the second call), the stored structured part carries `prose_status: "retrying"` and `prose_retry: {attempts, trigger, next_at}`. Each scheduler tick asks again for the summaries whose `next_at` has passed, 5 min, 30 min and 2 h after the tries before; after the fourth try, or at once after a 4xx (our request is wrong), `prose_status` is `"failed"` and nothing retries. A summary rewritten meanwhile (a Reopen) is never overwritten by a late retry. Written prose clears both keys. The state lives in the database, so a restart loses no retry.
- **The API carries the state in the open structured part.** `contracts/api.openapi.yaml` keeps `SummaryStructured` open "so the summary writer can add fields", so no contract change is needed: the web app reads `summary.structured.prose_status` and says "The written summary is not ready yet. It is retried automatically" or "could not be produced", in the Application detail and in the Recruiter's view of the stage.
- **Each scheduler phase is isolated, and the Jev budget is total.** A phase that raises is logged, rolled back and reports nothing; the next one runs. The Jev request runs on a small worker pool and the router waits at most the timeout for the whole call; the per-phase httpx limits stay at the same value as a backstop.

## Alternatives considered
- **Leave the closed Knockout to the agent and fix only the stub:** the model can still answer "No me ha quedado claro"; the closed state is code's knowledge, so code says it.
- **Add the closed note to every reply on a closed Knockout:** repeats it on every "gracias" and replaces the agent's re-ask on the second turn of a new answer.
- **A `prose_status` column and a migration, plus a field on `Summary`:** cleaner, but `Summary` is frozen and the structured part is already the contract's place for the writer's own fields.
- **Retry the prose in a background thread with backoff:** lost on a restart; the scheduler already runs every minute.

## Consequences
- The first reply after a Knockout no longer depends on the model; a question asked in that first message gets the note, not an answer (the agent answers the next one).
- No Screener skill change: the skill sits at its character cap, and the code guarantees work whatever the agent writes. No Hermes image rebuild.
- A failed retry updates only the structured part, with no `summary_written` event; a successful one writes the summary as usual (a `summary_written` event). Each retry's calls go to the cost ledger.
- Tests: `tests/channel/test_closed_replies.py`, `tests/channel/test_copy.py`, `tests/channel/test_knockout_inbound.py`, `tests/summary/test_prose_retry.py`, `tests/scheduler/test_phase_isolation.py`, `tests/jev/test_client.py`, `src/web/tests/unit/detail.test.tsx`, `src/web/tests/unit/stage.test.tsx`.
