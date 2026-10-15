# 0006. Agents recommend, humans decide

- Status: Superseded by 0012
- Date: 2026-10-09

## Context
Criba's domain is recruiting and HR, which the EU AI Act classes as high-risk (Annex III). GDPR Art. 22 bars solely automated decisions with significant effect. AI-disclosure duties apply from Aug 2026. See our industry research.

## Decision
- Workers return `recommendations`, never decisions.
- In v1 the orchestrator never sends messages to candidates or employees, and never writes to an external system. It produces files for a human to act on.
- The only checkpoint built in v1 is the final review: the user accepts the report or asks for changes. That is a new run, not an edit.
- The generic worker skill carries the hard rules: no protected attributes or obvious proxies, no emotion inference, and AI disclosure in any text meant for candidates.

## Alternatives considered
- The full H0–H5 checkpoint set from the sketch: only meaningful once the case defines who approves what.

## Consequences
- When the case brings outbound messages or people-affecting actions, add the matching checkpoint (sketch §8) through a new record.
