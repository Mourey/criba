-- 0003: persist held actions (wave 2 contract amendment, W2-C follow-up).
--
-- Router actions held for quiet hours (Q41), for example the Interview offer after
-- Approval, lived in the scheduler's memory and were lost on a restart. This adds the
-- table that holds them. It is a pure addition: no existing table is touched. The
-- statements below match contracts/schema.sql exactly (tests/db/test_migrate.py checks).

-- ---------------------------------------------------------------------------
-- Held actions: router actions held for quiet hours (Q41), so a restart keeps them
-- ---------------------------------------------------------------------------

CREATE TABLE held_actions (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    action         TEXT NOT NULL CHECK (action IN ('approved')),  -- the router action to run; one value today
    not_before     TEXT NOT NULL,                 -- UTC; when quiet hours end for the Candidate
    attempts       INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    status         TEXT NOT NULL DEFAULT 'held' CHECK (status IN ('held', 'dispatched', 'dropped', 'failed')),
    last_error     TEXT,                          -- the last failed attempt's error
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

-- At most one held row per action per Application; finished rows stay as history.
CREATE UNIQUE INDEX held_actions_one_held
    ON held_actions (application_id, action) WHERE status = 'held';
CREATE INDEX held_actions_due ON held_actions (status, not_before);
