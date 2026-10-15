-- 0010: message_signals gains what the Jev router read about an explicit language request
-- (ADR 0061).
--
-- asks             the language the message asks the chat to continue in ('es', 'en') or 'none'
-- asks_confidence  the confidence of that Choice
-- language_reason  why this message stored a language: 'first_message', 'switch' (a full
--                  sentence in the other language), 'request' (an explicit request) or
--                  'variant' (only the Spanish variant moved to the country's)
--
-- All three are NULL on rows written before this migration and on a failed call. Added with
-- ALTER TABLE ... ADD COLUMN, never a table rebuild; codes and probabilities only, no text.
-- contracts/schema.sql holds the CREATE TABLE message_signals text as sqlite_master stores it
-- after the ALTERs (the new columns follow the created_at line), so the two files do not match
-- statement for statement; the schema the migrations build matches the contract exactly
-- (tests/db/test_migrate.py checks).

ALTER TABLE message_signals ADD COLUMN asks TEXT CHECK (asks IN ('es', 'en', 'none'));
ALTER TABLE message_signals ADD COLUMN asks_confidence REAL CHECK (asks_confidence IS NULL OR asks_confidence BETWEEN 0 AND 1);
ALTER TABLE message_signals ADD COLUMN language_reason TEXT CHECK (language_reason IN ('first_message', 'switch', 'request', 'variant'));
