# 0005. Hand off through JSON envelopes in a per-run directory

- Status: Accepted
- Date: 2026-10-09

## Context
The brief says agents "report back with the files that were requested". Children share no context with the orchestrator (0003), and a chat summary cannot be checked.

## Decision
- Each user request becomes `runs/<run_id>/`.
- For each task, the orchestrator writes `tasks/<task_id>/envelope.json` (`criba.task.v1`): objective, input paths, constraints and the output contract.
- The worker writes the files named in the contract, plus `result.json` (`criba.result.v1`): status, summary, outputs with sha256, recommendations, assumptions, open questions and self-reported usage.
- Before reporting, the orchestrator reads `result.json` from disk and checks that every contracted file exists and that its hash matches. If any file is missing, the task is `partial`, whatever the worker claimed.
- The user-facing deliverable is `final/report.md` plus `final/manifest.json`.
- Both envelope schemas are JSON Schema files in `schemas/`, and a small validator script checks them.

## Alternatives considered
- Results returned only in chat: cannot be verified and leaves no audit trail.
- A database: more than v1 needs. The run directory is enough and is easy to inspect.

## Consequences
- Every run leaves a self-contained, auditable folder. This is also the start of the log trail that EU AI Act deployers must keep.
- Personal data stays in `runs/`. The retention window comes from the case.
