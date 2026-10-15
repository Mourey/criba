# 0002. Build a lean, case-agnostic core before the case arrives

- Status: Accepted. Scope widened to two layers by 0008–0011; the lean-core principle (case roles plug in, the core doesn't change) still holds.
- Date: 2026-10-09

## Context
The concrete job ("the case") has not been given. The brief asks for a simple shape: the user talks to a central orchestrator, the orchestrator delegates to agents, agents return the requested files, and the orchestrator reports back. The earlier architecture sketch adds a Jev router, Bot Mode rooms and six checkpoints. That is more than the brief asks for, and it bets on a case we have not seen.

## Decision
Build only what every plausible case needs:
- the `criba-hq` orchestrator profile;
- dispatch with `delegate_task`;
- task and result envelopes, and the run directory;
- one generic role skill, to show how a role plugs in;
- one end-to-end test.

When the case arrives, adding a role means adding a skill. The core does not change.

## Alternatives considered
- Spec the full sketch now (option B): high cost, and the routing and room design would be tuned to an imagined case.
- Design only, build nothing (option C): leaves the Hermes mechanics untested until the case arrives, under time pressure.

## Consequences
- The case-specific roles (recruiter, onboarding, insights, compliance) remain proposals, not commitments.
- See 0007 for what is deferred and what would bring it back.
