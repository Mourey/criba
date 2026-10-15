-- 0008: messages.modality and voice_sessions, the storage half of the voice agent (parent R7).
--
-- messages gains modality, 'typed' for every existing row and 'spoken' for a voice call's turns
-- and the replies to them. It is added with ALTER TABLE ... ADD COLUMN, never a table rebuild:
-- messages_idempotency and every other object on messages stay as they are. voice_sessions
-- records each voice call (who, when, how long) and never holds text, audio or anything derived
-- from audio. contracts/schema.sql holds the CREATE TABLE messages text as sqlite_master stores
-- it after the ALTER, so the two files do not match statement for statement; the schema the
-- migrations build matches the contract exactly (tests/db/test_migrate.py checks).

ALTER TABLE messages ADD COLUMN modality TEXT NOT NULL DEFAULT 'typed' CHECK (modality IN ('typed','spoken'));

CREATE TABLE voice_sessions (
    id                         TEXT PRIMARY KEY,           -- 'vs_<32 hex>'
    conversation_id            TEXT NOT NULL,              -- candidate_identities.external_id of the web chat conversation
    candidate_id               TEXT NOT NULL REFERENCES candidates(id),
    country                    TEXT NOT NULL CHECK (country IN ('ES', 'MX')),  -- the agent's country
    elevenlabs_conversation_id TEXT,                       -- the post-call webhook's join key (R19); written once
    started_at                 TEXT NOT NULL,
    ended_at                   TEXT,
    seconds                    REAL CHECK (seconds IS NULL OR seconds >= 0)
) STRICT;

CREATE INDEX voice_sessions_by_conversation ON voice_sessions (conversation_id, started_at);
CREATE INDEX voice_sessions_live ON voice_sessions (started_at) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX voice_sessions_by_elevenlabs
    ON voice_sessions (elevenlabs_conversation_id) WHERE elevenlabs_conversation_id IS NOT NULL;
