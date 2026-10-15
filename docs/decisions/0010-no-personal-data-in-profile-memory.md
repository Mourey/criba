# 0010. Profile memory holds no personal data

- Status: Accepted. Spike 1 (2026-10-09) **reproduced the leak** with the default config: the `memory` tool wrote a Candidate's name and Zone to `USER.md`, and a different session repeated them. It is mitigated by `memory.memory_enabled: false`, `memory.user_profile_enabled: false`, and disabling the `memory` and `session_search` toolsets. With those settings, the other session answered "unknown". See spike 1 (the Runs API).
- Date: 2026-10-09

## Context
A profile's memory (`MEMORY.md`, `USER.md`) is loaded into every session of that profile (`profiles.md:14, 83`). A fact about one candidate would reach every other candidate's session.

## Decision
- Client-facing profiles run with memory writes disabled, or limited to knowledge about how to operate that is reviewed by a human and contains no personal data.
- Everything about a person lives in their case record (0009) and their own session.
- PII redaction (`privacy.redact_pii`) is evaluated for each channel.

## Alternatives considered
- **An external memory provider with a namespace per user:** possible later. It adds a dependency, and the case record already covers the need.

## Consequences
- An agent never "learns" about a person across sessions except through the case record, which is auditable and can be deleted for data subject requests and retention.
