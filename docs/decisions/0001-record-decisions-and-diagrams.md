# 0001. Record decisions as ADRs, with diagrams

- Status: Accepted
- Date: 2026-10-09

## Context
The project will change shape when the case arrives. The owner wants every architectural decision documented, with diagrams, from the start.

## Decision
- Every architectural decision gets a numbered record in `docs/decisions/`.
- Diagrams are kept as Mermaid, one perspective per way of looking at the system.
- Each perspective's `note` names the records it depends on.
- Until code exists, the diagrams describe the designed system and mark unbuilt or deferred parts. Once code lands, they are refreshed from the code.

## Alternatives considered
- Diagrams inside the early architecture sketch only: one long file that drifts as the design changes, and nothing records why a choice was made.
- Decisions recorded only in git history: hard to find, and there is no repo yet.

## Consequences
- A design change touches three things: a new or superseding record, the diagram, and the spec.
- The pre-case architecture sketch is only a starting point. Where it conflicts with these records, the records win.
