-- 0004: a home for the Takeover hand-back summary outside the event log (Q38).
--
-- events.data holds only ids, codes and canonical values. The hand-back summary is free
-- text (the team member's, or a recap that quotes the transcript), so it moves to its own
-- table, which an Erasure request deletes with the Candidate's messages; the
-- takeover_handed_back event keeps a `handback_id` reference. A pure addition: no existing
-- table is touched. The statements below match contracts/schema.sql exactly
-- (tests/db/test_migrate.py checks). 0005 moves existing summaries here.

-- ---------------------------------------------------------------------------
-- Hand-backs: the recap a Takeover leaves for the agent's next turn
-- ---------------------------------------------------------------------------

CREATE TABLE handbacks (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    user_id        TEXT REFERENCES users(id),     -- who handed the chat back
    summary        TEXT NOT NULL,                 -- free text: the team member's, or a recap generated from the transcript
    generated      INTEGER NOT NULL DEFAULT 0 CHECK (generated IN (0, 1)),
    created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX handbacks_by_application ON handbacks (application_id, id);
