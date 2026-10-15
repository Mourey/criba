# 0004. Define each role once, as a skill

- Status: Accepted
- Date: 2026-10-09

## Context
A role may run as a `delegate_task` child today and as its own profile later (0003). If its instructions live in two places, the two copies will drift apart.

## Decision
- Each role is a single skill at `skills/criba/<role>/SKILL.md` in this repo. It holds the role's job, its constraints, the outputs it must produce, and the result envelope it must write.
- v1 ships one generic role, `criba/worker`. It runs any task envelope, so we can test the full path with no case.
- The orchestrator's own behaviour is the skill `criba/orchestrator`, which the `criba-hq` profile loads.
- Sources live in this repo and are installed into Hermes following the harness-library conventions: component metadata, and nothing installed automatically.

## Alternatives considered
- Role instructions inside each delegation prompt: no single source of truth, and impossible to review.
- Role instructions in per-profile `SOUL.md` files: ties roles to profiles, which v1 does not have.

## Consequences
- Adding a case role means adding one skill directory, plus a fixture for the end-to-end test.
