# 0009. Agents don't message each other; a router coordinates through the case record

- Status: Accepted
- Date: 2026-10-09

## Context
Criba's lifecycle passes a person from one agent to the next: recruiting, then onboarding, then follow-up and retention. Bot Mode's `message_agent` exists only in Bot Chat sessions, never in gateway or regular chat sessions (`bot-mode.md:186`). Agents that message each other freely are also hard to audit. Recruiting and HR AI is high-risk under the EU AI Act, which requires logs and human oversight.

## Decision
- Each person has a **case record**: their current stage, events, the files produced, and approvals. It is the only shared state between agents.
- Agents never message each other. A handoff is an event written to the case record (for example `hired`, `day_7`, `risk_flag`).
- A **router** reacts to events and inbound messages, decides which agent acts next, and opens or continues that person's session on that agent's profile. v1's router is code with explicit rules. A Jev classifier can be added later (0007).
- Each agent sees only the case record of the person it is serving.

## Alternatives considered
- **Direct bot-to-bot messaging:** not available in client-facing sessions, serialised per bot, and leaves no structured trail.
- **A central LLM orchestrator that relays every message:** adds a model call to every turn and becomes one point that can be manipulated.

## Consequences
- The schema for the case record and its events becomes a core contract, alongside the envelopes from 0005.
- Latency is one router decision plus a direct session call. Nothing waits on polling.
- Agents can be tested one at a time against a fixture case record.
