# 0056. The Summary is Recruiter-facing prose in plain words: the communication notes live only on `communication_notes`, and no system code reaches the prose

- Status: Accepted
- Date: 2026-10-10
- Amends: 0051 (where the communication notes are shown: not in the Summary)
- Refines: 0025 (what the summary model reads, and what is checked in what it writes) and 0042 (the check is on our own codes in our own output, never on the Candidate's words)

## Context
The owner read the Summary on the Demo stage and in the Recruiter's view and decided (2026-10-10) that it is **prose a Recruiter reads, and nothing else**. Two things in it were not:

- **Coaching notes.** ADR 0051 made the writer append a paragraph, "Cómo abordarlo: ..." / "How to approach: ...", to `summary_prose`, and add a `communication_notes` list to `summary.structured`. Advice on how to talk to the Candidate is not a summary of the application, and the Recruiter panel (the communication-notes handoff) is the place for it.
- **System text.** The sim writer (`DeterministicSummaryClient`, used on the Demo stage and in sim mode) wrote "Etapa alcanzada: qualified" and "Avisos activos: review_requested, stalled": a stage key and flag keys as they sit in the database. The real model had the same invitation: the user prompt carried the structured part as raw JSON (`"stage": "qualified"`, `"full_name"`, `"flags": [...]`), so a stage key, a field key or a flag key could be copied into the prose.

## Decision
- **The notes are not in the Summary.** The writer no longer appends the paragraph or adds `communication_notes` to `summary.structured`. `communication_notes` and `message_signals` stay on `GET /api/applications/{id}` exactly as 0051 defined them (same fields, same label, same scope); that is where the Recruiter panel reads them. Nothing in production calls `format_for_summary` any more; it stays in `criba.jev.notes` with its test only because deleting that test's assertion would lower the floor guard's bar, and it can go with its test when the Recruiter panel is built.
- **The model reads a fact sheet in words, not JSON.** `criba.summary_text.render_facts` turns the structured part into lines such as "The application passed Screening and is waiting for a Recruiter's approval", "Driving licence: yes", "Availability: full time", "Notices for the Recruiter: a review was requested". There is no stage, field or flag key in it, and no id. The structured part itself is unchanged: it is the contract shape, and the API still serves it. The system prompt tells the model to write only in a Recruiter's words.
- **The sim writer says the same things in words.** The stage line is gone; the notices are the flag labels in the Summary's language (`FLAG_LABELS`, es and en).
- **A check on the output, with our own closed lists.** `criba.summary_text.leaked_codes` looks in the prose for our own internal codes: stage, field, flag and outcome keys that contain an underscore (`full_name`, `review_requested`), a single-word key written as a labelled value (`Stage reached: qualified`, `` `qualified` ``), and ids (the Application's, and the `app_`, `cand_`, `zone_` shapes). It never looks at what the Candidate wrote and has no word list against it (0042). A natural word that is also a key (a Recruiter reading "qualified" in English) is not a leak.
- **A leak is treated like a cut-off.** The writer asks once more, naming the codes that leaked; if the second answer still carries one, `summary_prose` is stored as `null` and an error is logged, as for a prose that is cut off twice (`SummaryWriter._ask`). The structured part is never held back. Code is the guarantee for what we can check; the prompt and the fact sheet make the leak rare.
- **The live smoke checks both** in flow 1 (`qualified`): the real Sonnet prose has no internal code and no notes.

## Alternatives considered
- **Keep the paragraph, move it to the end of the structured part only:** still a coaching text inside the Summary object; the Recruiter panel would have two copies to keep in step.
- **Strip codes from the model's text by replacement:** a rewrite could change the meaning or produce a broken sentence; asking again, or storing none, is safe.
- **Only fix the sim writer:** the real model was given the same raw keys, so it could produce the same line.
- **A word list for "system-sounding" phrases:** it would judge the model's prose by taste and drift; a closed list of our own codes is exact.

## Consequences
- **No contract change.** `SummaryStructured` is open (`additionalProperties: true`) and never declared `communication_notes`; the field simply is no longer written. `ApplicationDetail.communication_notes` and `message_signals` are untouched.
- **Existing summaries** written before this change keep their paragraph in `summary_prose` and the list in `summary_structured` until the Application is Reopened (the summary is rewritten then). No migration: the text is context a person already read, and there is no Recruiter panel that depends on it yet.
- **Docs:** the communication-notes handoff says the notes are on the detail API only. 0051's Status line points here.
- **Tests:** `tests/summary/test_plain_words.py` (fact sheet, sim writer, the code check, the retry and the drop), `tests/jev/test_signals_api.py` (no notes in the Summary even when signals fired; the API still returns them), and two checks in the live smoke (`src/tests/live/flows.py`).
