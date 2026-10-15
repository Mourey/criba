-- 0006: remove the Candidate's name from existing answer_recorded events.
--
-- An answer_recorded event for full_name says only that the name was recorded; the name
-- itself lives on the Application (applications.full_name) and the Candidate
-- (candidates.full_name), so an Erasure request never has to scrub the event log.
-- Rows written before this rule carried it under data.value.
--
-- Data only: no schema object changes. The migrator runs this in one transaction.

UPDATE events SET data = json_remove(data, '$.value')
    WHERE kind = 'answer_recorded'
      AND json_extract(data, '$.field') = 'full_name'
      AND json_type(data, '$.value') IS NOT NULL;
