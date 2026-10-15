# 0066. Operators see the stored Jev readings, and nobody else sees the probabilities

- Status: Accepted
- Date: 2026-10-11
- Refines: 0051

## Context
ADR 0051 stores one reading per typed Candidate message in `message_signals` (language and confidence, the switch score, four tone scores, the signals that fired, the tone hint, any Escalation, the status, the model and the policy version) and says "Raw probabilities are never in the API". Recruiters see flags and communication notes only. The Operator, who calibrates the thresholds and reviews incidents, cannot see the scores behind a conversation: the Application detail returns a flag only for the messages on which a signal fired or the language changed, to every role.

The wave 2 operator console asks for the full readings per message, calm and unreadable ones included. Two facts shape the answer. A reading exists only for a typed text message Jev read: none for a button tap, media or blank text, an opt-out or a message under a Takeover, a system turn, any message while `CRIBA_JEV=off`, or a read that hit an unexpected exception. Rows are purged after 180 days (`CRIBA_SIGNALS_RETENTION_DAYS`), and only the current threshold bundle exists, so a stored `policy_version` cannot be resolved to its thresholds.

## Decision
- **Operators, and only Operators, may see the stored probabilities**, through `GET /api/applications/{application_id}/jev-readings` (`getJevReadings`, `x-roles: [operator]`), for calibration and incident review. A Recruiter gets 403 for every Application, inside or outside its Locations; an anonymous caller gets 401. The endpoint is read-only and is a separate path, so `getApplication`, its `message_signals` flags and the Recruiter's tests are unchanged.
- **What stays withheld.** The response holds ids, codes and numbers: never a message body or any Candidate free text, and no spoken message (typed text only; voice readings are a later decision, ADR 0065). 0051's guarantees to Recruiters stand: flags rather than probabilities, no per-Candidate score in any Recruiter-facing view.
- **One entry per typed Candidate message**, oldest first, with the status `ok`, `error` or `timeout` as stored, `expired` when no row remains and the message is older than the retention period, and `not_read` when there is no row otherwise (Jev off, a Takeover, an opt-out or a failed read, which the stored data cannot tell apart). A reason code per skipped message would need the router to record skips, which this record does not ask for. Only an `ok` entry carries a reading; a failed read has no values.
- **The current thresholds come with the readings** (`thresholds`, with its `version`), and each reading carries the `policy_version` it was made under. A consumer may draw a threshold marker only when the two versions are equal. The mapping of each bar to its threshold fields is written in the contract.
- **Display only.** The readings are never an input to a Knockout, Rejection or Approval, nor to ranking, routing or booking. The guard in `tests/jev/test_not_a_decision_input.py` enforces it: the pattern of names it searches for gains the reader's import forms, and the allow-list gains exactly `api/readings.py` (the reader) and `api/routes.py` (its registration). A deciding module that imports the reader fails the guard.
- **Room for `jev-intent`.** The reading object is closed, with an optional `intent` and `malice` that this record's endpoint does not return today.
- **Preview.** "Preview as Recruiter" is a state of the browser (ADR 0052 names it), not of the session: the server still sees an Operator, so a previewing Operator's own session can call the endpoint and get the readings. What the panel shows while previewing is a web decision, not this record's.

## Alternatives considered
- **A field on the Application detail, Operator-only.** Rejected: the detail is served to every role through one schema and one set of tests, and a role-gated probability on it is one serialisation slip from a Recruiter. A separate path answers 403 before any handler runs.
- **Edit 0051 to add an Operator exception.** Rejected: accepted records change only in their Status line. This record refines it for one role.
- **Return only the messages with a row.** Rejected: "calm" and "unreadable" would then look like "not there", and a gap in the readings would be silent. The two extra statuses make the gap visible.
- **Thresholds per stored policy version.** Not possible: only the current bundle exists, and inventing older ones would draw markers that were never applied.

## Consequences
- An Operator can compare each message's scores with the thresholds, and see which messages Jev never read, without database access.
- Stored inferences about an identifiable person now reach a screen. The 0051 reading of the law stands (not biometric, never a decision, 180-day retention), and the exposure is smaller than a Recruiter's: one role, display only, no text. Grupo Sazón's legal team still confirms the reading.
- A new allow-list entry in the guard is a loosening a later change could widen; the guard's own tests pin its size and the names of the two modules.
- `jev-intent` adds its fields to the same response without a breaking change; the panel (`jev-readings-web`) consumes the regenerated types.
