# 0008. Two layers: a profile per client-facing role, a session per person

- Status: Accepted
- Date: 2026-10-09
- Supersedes: 0003 for the client-facing layer

## Context
This is a client-facing product. Candidates and employees will talk to the agents over WhatsApp, a web UI or other channels, and client HR teams will send work too. Hermes keeps three stores of context:
- **Session**: one per chat on the gateway (`website/docs/user-guide/messaging/index.md:100`).
- **Profile memory**: `MEMORY.md` and `USER.md`, loaded into every session of the profile (`profiles.md:14, 83`).
- **Bot Chat**: one permanent conversation per Bot Mode bot (`bot-mode.md:36-45`).

A bot is a profile plus its Bot Chat. It is not a process that is always running: its backend shuts down after 10 idle minutes, and at most 3 are kept running by default (`bot-mode.md:373`). The gateway is the part that is always on.

## Decision
The system has two layers.
- **Client-facing layer.** Each role that talks to people is its own Hermes profile: recruiting, onboarding and insights in Criba's model (María, Daniel, Claire). A profile is reached through its gateway (WhatsApp and similar) or the API server (web UI). Each person gets their own session on each profile, so a candidate's conversation never shares context with another candidate's.
- **Internal layer.** `criba-hq` serves client HR, Criba ops and the owner. It handles bulk and planning work ("fill 40 roles in Valencia") with the task envelopes and run directory from 0005. It uses `delegate_task` for fan-out, as 0003 describes.

## Alternatives considered
- **Bot Chat per role, carrying all users:** every candidate would share one context, turns would run one at a time, and compaction would blend people together. That breaks GDPR by construction.
- **One profile for everything, separated only by skills:** one shared memory and one set of tools for every role, with no per-role model or tool limits.
- **Kanban dispatch to profiles:** rejected by the owner because a polled board adds latency to live conversations.

## Consequences
- How code opens or continues a session on profile X for person Y is unverified. It is the first spike: the API server with `/p/<profile>/`, or gateway session keys.
- Bot Mode stays available on the internal layer, for an operator roster and rooms, but is not on the client-facing path (0009).
