-- 0012: tool_calls, one row per agent tool call, the storage half of the Operator's tool-call trace (ADR 0069).
--
-- Written best effort by POST /internal/tools/{name} after the tool returns; linked to its run
-- when the run returns or fails (run_id, run_closed). It holds codes, ids, times and canonical
-- values only: never a tool's value or hint, a note, a query, a name, a slot ref or message
-- text (ADR 0035). The scheduler purges rows after the message_signals retention period, and a
-- row goes with its Application (ON DELETE CASCADE). Erasure deletes nothing in the system
-- today, so an Erasure request removes no trace rows yet (the gap ADR 0069 records). A pure
-- addition: no existing table is touched. The statements below match contracts/schema.sql
-- exactly (tests/db/test_migrate.py checks).

CREATE TABLE tool_calls (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    run_id         TEXT,                          -- the run that made the call; set when the run returns or fails
    run_closed     INTEGER NOT NULL DEFAULT 0 CHECK (run_closed IN (0, 1)),  -- 1: a failed run without an id closed this call, so no later run claims it
    tool           TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    duration_ms    INTEGER NOT NULL CHECK (duration_ms >= 0),
    outcome        TEXT NOT NULL CHECK (outcome IN ('ok', 'invalid', 'knockout', 'error')),
    error          TEXT,                          -- one of the contracts/tools.json error codes, null when ok
    args           TEXT NOT NULL DEFAULT '{}',    -- JSON: argument names with canonical values or null, never free text (ADR 0035, ADR 0069)
    created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX tool_calls_by_application ON tool_calls (application_id, id);
CREATE INDEX tool_calls_by_run ON tool_calls (run_id);
CREATE INDEX tool_calls_by_ts ON tool_calls (created_at);
