-- 0013: unreadable messages (ADR 0063).
--
-- queue_items.reason gains 'unreadable': the Escalation the router raises at the third unreadable
-- message in a row, which hands the conversation to the Recruiters until it is resolved. SQLite
-- cannot alter a CHECK constraint, so queue_items is rebuilt with every row kept, as 0002 did
-- for events: the old table is renamed away first so the new one is created under its own name
-- and its stored SQL matches contracts/schema.sql exactly. No table refers to queue_items.
--
-- message_signals gains `unreadable`, the probability Jev gave that the message is nonsense.
-- NULL on rows written before this migration and on a failed call. Added with ALTER TABLE ...
-- ADD COLUMN, never a rebuild; a probability only, no text. contracts/schema.sql holds the
-- CREATE TABLE message_signals text as sqlite_master stores it after the ALTERs of 0010 and this
-- one.

DROP INDEX queue_items_one_open;
DROP INDEX queue_items_open;

ALTER TABLE queue_items RENAME TO queue_items_before_0013;

CREATE TABLE queue_items (
    id             TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    kind           TEXT NOT NULL CHECK (kind IN ('approval', 'review', 'escalation')),
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    reason         TEXT CHECK (reason IN (
                       'repeated_invalid_answers', 'abuse', 'unanswered_question',
                       'wants_human', 'frustrated', 'technical_error', 'other',
                       'unreadable')),  -- escalations only
    note           TEXT,                          -- agent's or Candidate's words
    resolution     TEXT CHECK (resolution IN (
                       'approved', 'rejected',    -- approval
                       'upheld', 'overturned',    -- review
                       'dismissed', 'takeover')), -- escalation
    resolution_note TEXT,
    resolved_by    TEXT REFERENCES users(id),
    created_at     TEXT NOT NULL,
    resolved_at    TEXT,
    CHECK ((status = 'resolved') = (resolution IS NOT NULL)),
    CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
    CHECK ((kind = 'escalation') = (reason IS NOT NULL)),
    CHECK (resolution IS NULL
           OR (kind = 'approval'   AND resolution IN ('approved', 'rejected'))
           OR (kind = 'review'     AND resolution IN ('upheld', 'overturned'))
           OR (kind = 'escalation' AND resolution IN ('dismissed', 'takeover')))
) STRICT;

INSERT INTO queue_items (id, application_id, kind, status, reason, note, resolution,
                         resolution_note, resolved_by, created_at, resolved_at)
    SELECT id, application_id, kind, status, reason, note, resolution,
           resolution_note, resolved_by, created_at, resolved_at
    FROM queue_items_before_0013;

DROP TABLE queue_items_before_0013;

CREATE UNIQUE INDEX queue_items_one_open
    ON queue_items (application_id, kind) WHERE status = 'open';
CREATE INDEX queue_items_open ON queue_items (status, kind, created_at);

ALTER TABLE message_signals ADD COLUMN unreadable REAL CHECK (unreadable IS NULL OR unreadable BETWEEN 0 AND 1);
