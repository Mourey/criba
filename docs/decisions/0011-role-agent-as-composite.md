# 0011. Each role agent is a composite: one role orchestrator plus specialist sub-agents

- Status: Accepted
- Date: 2026-10-09

## Context
The owner reports that Criba's María is "20 agents bolted together by an orchestrator". We haven't verified this against a public source. Either way, one role covers many separate jobs: writing job descriptions, multi-posting, screening, scoring, scheduling, no-show reminders and answering FAQs. Each job needs different instructions and tools.

## Decision
- The session a person talks to belongs to a **role orchestrator** (the profile's main agent). It keeps the conversation and the person's context, and decides which specialist to use.
- **Specialists are skills** (`skills/criba/<role>/<specialist>/`). They run as `delegate_task` children for work that takes many steps or runs in parallel (for example scoring 140 applicants against a rubric), or are loaded inline with `skill_view` for quick steps.
- A child has no conversation context and no memory (`delegate_tool.py:272-276`, `skip_memory=True`). It gets only what the role orchestrator passes, which fits 0010.
- Specialists write files and return results through the envelopes from 0005. The role orchestrator writes the outcome to the case record (0009).

## Alternatives considered
- **One profile per specialist:** 20 or more gateways and profiles per role, with handoffs between them through the router. Far more to run, and no gain in isolation over child agents.
- **One monolithic prompt per role:** hard to test, and its instructions drift.

## Consequences
- There are three tiers: the router chooses the role, the role orchestrator chooses the specialist, and the specialist does the work.
- The number of specialists varies by role and grows with the case without changing the layers.
- Delegated children run in the background (spec §2). A live conversation needs a holding reply ("checking your availability…") while children work. To be confirmed in the spike.
