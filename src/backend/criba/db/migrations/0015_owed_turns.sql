-- 0015: owed_turns, the durable record of a reply we owe a Candidate (ADR 0073).
--
-- One row per inbound Candidate message that starts a run on a typed channel, written when the
-- router accepts it and deleted when the turn is answered. A failed attempt keeps the row with
-- its next attempt time; the recovery (scheduler, and at startup) re-runs it with the same
-- Idempotency-Key and the same input, so Hermes replays a run it already took. run_input holds
-- Candidate text, so a row goes with its message (ON DELETE CASCADE). 0014 is taken by a
-- branch in flight. A pure addition; the statements match contracts/schema.sql exactly.

CREATE TABLE owed_turns (
    message_id      INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,  -- the inbound Candidate message we owe a reply to
    application_id  TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    channel         TEXT NOT NULL CHECK (channel IN ('webchat', 'whatsapp')),  -- the reply's channel; spoken turns are never owed (ADR 0073)
    turn_input      TEXT NOT NULL,                -- the agent's input as built at accept time (the message as run_input gives it, or the tap result)
    run_input       TEXT,                         -- the exact input first POSTed to Hermes; null until then. A re-POST under the same key must be byte-identical
    idempotency_key TEXT NOT NULL,                -- the key the next attempt POSTs with: the message_id, or a new one after Hermes reported the run failed
    after_event_id  INTEGER,                      -- TurnRequest.after_event_id: the last event before a tap was recorded
    booking_url     TEXT,                         -- an "other time" tap's link (ADR 0064)
    booking_message TEXT,
    typed           INTEGER NOT NULL DEFAULT 0 CHECK (typed IN (0, 1)),  -- 1: a typed text message, read by Jev before its first run
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),    -- failed attempts so far
    status          TEXT NOT NULL DEFAULT 'owed' CHECK (status IN ('owed', 'exhausted')),
    holding_sent    INTEGER NOT NULL DEFAULT 0 CHECK (holding_sent IN (0, 1)),  -- the holding message went out in this outage
    next_attempt_at TEXT NOT NULL,                -- UTC; when the recovery may run it (a lease while the first attempt runs)
    last_error      TEXT CHECK (last_error IS NULL OR last_error IN ('error', 'timeout')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
) STRICT;

CREATE INDEX owed_turns_by_application ON owed_turns (application_id, message_id);
CREATE INDEX owed_turns_due ON owed_turns (status, next_attempt_at);
