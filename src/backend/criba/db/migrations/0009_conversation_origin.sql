-- 0009: candidate_identities.client and persona_id, the storage half of the Operator conversation list.
--
-- A web chat conversation is a candidate_identities row (channel 'webchat', external_id = the
-- conversation id). The start request's client and, for the Demo stage, persona_id are recorded
-- on that row so the Operator list can tell Test chat and Demo conversations from simulator
-- runs. Both columns hold codes only (ADR 0035, ADR 0060): client is one of three values and
-- persona_id matches ^[a-z0-9_]{1,64}$. Rows written before this migration keep both null and
-- are not listed. The columns are added with ALTER TABLE ... ADD COLUMN, never a table rebuild,
-- and live and die with their identity row. contracts/schema.sql holds the CREATE TABLE text as
-- sqlite_master stores it after the ALTER, so the two files do not match statement for statement;
-- the schema the migrations build matches the contract exactly (tests/db/test_migrate.py checks).

ALTER TABLE candidate_identities ADD COLUMN client TEXT CHECK (client IN ('test_chat', 'simulator', 'demo'));
ALTER TABLE candidate_identities ADD COLUMN persona_id TEXT CHECK (persona_id IS NULL OR (length(persona_id) BETWEEN 1 AND 64 AND persona_id NOT GLOB '*[^a-z0-9_]*'));
