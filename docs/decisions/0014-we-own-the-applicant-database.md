# 0014. We own the applicant database; inbound applicants are created in it

- Status: Accepted
- Date: 2026-10-09
- Amends: 0013 (its Context says "we only ingest applicants in a database")

## Context
The owner clarified that applicants don't need to be in the database already. If someone writes to us directly, we start the conversation and add them to the database. ADR 0012 forbids writes to external systems, so the database we add them to has to be ours.

## Decision
- The **applicant database is ours.** It holds one row per applicant, keyed by a stable person ID, along with channel identities (phone number, web chat ID), source tag, and the case record pointer (ADR 0009).
- **On a candidate's first message:** look up their channel identity. If they're unknown, create the applicant and their case record before the first reply. Then start screening (ADR 0013).
- **Client-provided applicants** (from Grupo Sazón's own sources) are imported into the same database by a read-only ingest from the client's system. Imported applicants who haven't written to us are the outreach path.
- Duplicates across paths (someone imported and then also writing in) are merged on channel identity, which in practice means the normalised phone number.

## Alternatives considered
- **Write inbound applicants into the client's database:** a write to an external system, which ADR 0012 rules out. If the client wants this later, deliver it as an export or add an integration under a new ADR.

## Consequences
- Creating the record is on the fast path, so the lookup or create must not add noticeable latency before the first reply.
- Our database is personal data under GDPR and Mexico's LFPDPPP, so it needs retention rules and deletion on request. One applicant row plus one case record makes deletion a single operation.
