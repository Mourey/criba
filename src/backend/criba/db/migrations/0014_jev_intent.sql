-- 0014: intent and malice readings (ADR 0071).
--
-- message_signals gains what Jev read about what a typed message is trying to do and whether it
-- tries to manipulate the assistant:
--
-- intent             the Choice: 'answering', 'asking', 'off_topic', 'wants_to_stop', 'steering'
--                    or 'other' (greetings, thanks, small talk)
-- intent_confidence  the confidence of that Choice
-- malice             the Noul: the probability that the message tries to manipulate the
--                    assistant. The column keeps the name of the question; the product label is
--                    Manipulation (ADR 0071)
--
-- All three are NULL on rows written before this migration, on a failed call, and on a spoken
-- turn (both questions are asked on every call, but nothing is stored for a spoken turn).
-- Display only, for Operators: no decision reads them. Added with ALTER TABLE ... ADD COLUMN,
-- never a table rebuild; codes and probabilities only, no text. contracts/schema.sql holds the
-- CREATE TABLE message_signals text as sqlite_master stores it after the ALTERs of 0010, 0013
-- and this one (the new columns follow the created_at line), so the two files do not match
-- statement for statement; the schema the migrations build matches the contract exactly
-- (tests/db/test_migrate.py checks).

ALTER TABLE message_signals ADD COLUMN intent TEXT CHECK (intent IN ('answering', 'asking', 'off_topic', 'wants_to_stop', 'steering', 'other'));
ALTER TABLE message_signals ADD COLUMN intent_confidence REAL CHECK (intent_confidence IS NULL OR intent_confidence BETWEEN 0 AND 1);
ALTER TABLE message_signals ADD COLUMN malice REAL CHECK (malice IS NULL OR malice BETWEEN 0 AND 1);
