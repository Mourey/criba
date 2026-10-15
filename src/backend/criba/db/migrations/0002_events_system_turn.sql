-- 0002: allow the `system_turn` event kind (wave 2 contract amendment).
--
-- The router logs each system turn (e.g. "[system] approved", Q41) as an event. System
-- turns are never stored as messages, so they never reach the Candidate's transcript.
-- SQLite cannot alter a CHECK constraint, so `events` is rebuilt with every row kept.
-- The old table is renamed away first so the new one is created under its own name and
-- its stored SQL matches contracts/schema.sql exactly (tests/db/test_migrate.py checks).
-- No other table, trigger or view refers to `events`. The migrator runs this inside one
-- transaction, so a failure leaves the old table in place.

DROP INDEX events_by_application;
DROP INDEX events_by_kind;

ALTER TABLE events RENAME TO events_before_0002;

CREATE TABLE events (
    id             INTEGER PRIMARY KEY,
    application_id TEXT REFERENCES applications(id),  -- NULL for Candidate-level events (outreach, opt-out lifted)
    candidate_id   TEXT NOT NULL REFERENCES candidates(id),
    kind           TEXT NOT NULL CHECK (kind IN (
                       -- lifecycle
                       'application_started', 'stage_changed', 'reopened',
                       'answer_recorded', 'answer_invalid', 'answer_ambiguous',
                       'screening_finished', 'summary_written',
                       -- Outcomes
                       'knockout', 'rejection', 'abandoned', 'withdrawn',
                       -- Review, Escalation, Approval
                       'review_requested', 'review_upheld', 'review_overturned',
                       'escalation_raised', 'escalation_dismissed',
                       'approval_requested', 'approved',
                       -- Claim
                       'claim_taken', 'claim_released', 'claim_taken_over',
                       -- Takeover
                       'takeover_started', 'takeover_handed_back',
                       -- Stalled and follow-ups
                       'stalled', 'nudge_sent', 'flag_qualified_waiting', 'flag_claimed_idle',
                       -- Interview
                       'slots_offered', 'booking_link_sent', 'interview_booked',
                       'interview_rescheduled', 'interview_slot_released',
                       -- Candidate-level
                       'outreach_sent', 'import_unresponsive', 'opted_out', 'opt_out_lifted',
                       'erasure_requested',
                       -- Runtime
                       'tone_hint', 'run_error', 'template_sent', 'system_turn')),
    actor          TEXT NOT NULL CHECK (actor IN ('agent', 'system', 'candidate', 'user')),
    user_id        TEXT REFERENCES users(id),
    data           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
    created_at     TEXT NOT NULL,
    CHECK ((actor = 'user') = (user_id IS NOT NULL))
) STRICT;

INSERT INTO events (id, application_id, candidate_id, kind, actor, user_id, data, created_at)
    SELECT id, application_id, candidate_id, kind, actor, user_id, data, created_at
    FROM events_before_0002;

DROP TABLE events_before_0002;

CREATE INDEX events_by_application ON events (application_id, created_at, id);
CREATE INDEX events_by_kind ON events (kind, created_at);
