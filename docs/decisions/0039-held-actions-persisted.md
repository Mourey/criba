# 0039. Actions held for quiet hours are persisted and dispatched once

- Status: Accepted, amended by 0046 (an Approval for a Candidate who wrote in the last 60 minutes is dispatched without waiting for `not_before`)
- Date: 2026-10-09
- Refines: 0038 (the scheduler holds actions for quiet hours)

## Context
Under 0038 the scheduler holds a router action, so far only the Interview offer after Approval, until quiet hours end. The first implementation kept held actions in memory, so a restart between 22:00 and 09:00 lost every Approval made in that window: the Candidate never got the offer, and nothing showed it. Fixed.

## Decision
- **Held actions live in a `held_actions` table** (migration `0003_held_actions.sql`, matching `contracts/schema.sql`): the Application, the action (`approved` today), `not_before` in UTC, attempts, status (`held`, `dispatched`, `dropped`, `failed`) and the last error.
- **At most one held row per action per Application** (a partial unique index); finished rows stay as history.
- **`run_due_jobs` dispatches due rows once.** An attempt is claimed by compare-and-set before dispatch, so a dispatched row never runs again. A row is dropped if the Application closed or the Candidate opted out, and marked failed after 3 attempts.

## Alternatives considered
- **Rebuild held actions at startup from events** (an `approved` with no offer yet): needs to infer intent from absence, and gets retries and failures wrong.
- **Keep them in memory and accept the loss:** a silent failure on the step that matters most to the Candidate.

## Consequences
- Held actions survive restarts and redeploys.
- The table is a pure addition; no existing table changed.
- New held action kinds extend the `action` CHECK through a migration and a contract amendment.
