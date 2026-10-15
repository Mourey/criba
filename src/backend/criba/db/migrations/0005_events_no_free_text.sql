-- 0005: remove Candidate free text from existing events.data (Q38).
--
-- events.data holds only ids, codes and canonical values; the Candidate's own words live
-- in messages and queue items, so an Erasure request never has to scrub the event log.
-- Rows written before this rule carried free text under these keys:
--
--   answer_recorded / answer_invalid / answer_ambiguous   raw_input  (in messages; zone
--                                                         and driver_license also on the
--                                                         application's *_input columns)
--   review_requested / escalation_raised / approval_requested  note  (on the queue item)
--   withdrawn / erasure_requested                         note       (dropped)
--   knockout                                              message    -> message_id
--   takeover_handed_back                                  summary    -> handbacks row
--
-- Data only: no schema object changes. The migrator runs this in one transaction.

-- Hand-back summaries move to handbacks. The row takes the event's id, so the reference
-- is known without a lookup; later rows continue after the largest id.
INSERT INTO handbacks (id, application_id, user_id, summary, generated, created_at)
    SELECT id, application_id, user_id, json_extract(data, '$.summary'),
           CASE WHEN json_extract(data, '$.generated') THEN 1 ELSE 0 END, created_at
    FROM events
    WHERE kind = 'takeover_handed_back'
      AND application_id IS NOT NULL
      AND json_type(data, '$.summary') = 'text';

UPDATE events
    SET data = json_set(json_remove(data, '$.summary'), '$.handback_id', id)
    WHERE kind = 'takeover_handed_back'
      AND id IN (SELECT id FROM handbacks);

-- Knockout text: point at the agent message that carried it, when one matches verbatim.
UPDATE events
    SET data = json_set(data, '$.message_id', (
        SELECT max(m.id) FROM messages m
        WHERE m.application_id = events.application_id
          AND m.sender = 'agent'
          AND m.body = json_extract(events.data, '$.message')))
    WHERE kind = 'knockout'
      AND json_type(data, '$.message') = 'text'
      AND json_type(data, '$.message_id') IS NULL
      AND EXISTS (
        SELECT 1 FROM messages m
        WHERE m.application_id = events.application_id
          AND m.sender = 'agent'
          AND m.body = json_extract(events.data, '$.message'));

-- Then every free-text key goes, whatever happened above.
UPDATE events SET data = json_remove(data, '$.raw_input')
    WHERE kind IN ('answer_recorded', 'answer_invalid', 'answer_ambiguous')
      AND json_type(data, '$.raw_input') IS NOT NULL;

UPDATE events SET data = json_remove(data, '$.note')
    WHERE kind IN ('review_requested', 'escalation_raised', 'approval_requested',
                   'withdrawn', 'erasure_requested')
      AND json_type(data, '$.note') IS NOT NULL;

UPDATE events SET data = json_remove(data, '$.message')
    WHERE kind = 'knockout'
      AND json_type(data, '$.message') IS NOT NULL;

UPDATE events SET data = json_remove(data, '$.summary')
    WHERE kind = 'takeover_handed_back'
      AND json_type(data, '$.summary') IS NOT NULL;
