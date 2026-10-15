-- 0007: message_signals, what the Jev router read from each typed Candidate message (ADR 0051).
--
-- Per-message language and tone probabilities, tied to the message and the Application, never
-- to the Candidate and never holding text. The scheduler purges rows after the retention
-- period; an Erasure request deletes them with the message (ON DELETE CASCADE). Nothing that
-- decides an Application reads this table. A pure addition: no existing table is touched. The
-- statements below match contracts/schema.sql exactly (tests/db/test_migrate.py checks).

CREATE TABLE message_signals (
    id             INTEGER PRIMARY KEY,
    message_id     INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES applications(id),
    stage          TEXT,                          -- applications.stage when the message arrived
    status         TEXT NOT NULL CHECK (status IN ('ok', 'error', 'timeout')),
    model          TEXT,                          -- the model that served the call
    policy_version TEXT,
    lang           TEXT CHECK (lang IN ('es', 'en', 'other', 'unclear')),
    lang_confidence REAL CHECK (lang_confidence IS NULL OR lang_confidence BETWEEN 0 AND 1),
    switch         REAL CHECK (switch IS NULL OR switch BETWEEN 0 AND 1),
    frustrated     REAL CHECK (frustrated IS NULL OR frustrated BETWEEN 0 AND 1),
    confused       REAL CHECK (confused IS NULL OR confused BETWEEN 0 AND 1),
    wants_human    REAL CHECK (wants_human IS NULL OR wants_human BETWEEN 0 AND 1),
    abusive        REAL CHECK (abusive IS NULL OR abusive BETWEEN 0 AND 1),
    first_message  INTEGER NOT NULL DEFAULT 0 CHECK (first_message IN (0, 1)),
    language_set   TEXT CHECK (language_set IN ('es-ES', 'es-MX', 'en')),  -- the language this message stored, if it changed one
    fired          TEXT NOT NULL DEFAULT '[]',    -- JSON array of the signals above their thresholds
    hint           TEXT,                          -- the tone hint code given to the agent, if any
    escalation     TEXT CHECK (escalation IN ('wants_human', 'abuse')),  -- the Escalation this message raised, if any
    created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX message_signals_by_application ON message_signals (application_id, id);
CREATE INDEX message_signals_by_ts ON message_signals (created_at);
