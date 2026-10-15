# 0003. Dispatch work with Hermes `delegate_task` from one orchestrator profile

- Status: Superseded in part by 0008 (client-facing layer). Still applies to `criba-hq` on the internal layer and to fan-out within a role (0011). Hermes details verified 2026-10-09 (spec §2).
- Date: 2026-10-09

## Context
The brief suggests Hermes profiles as "bots, each with a certain function". Hermes has two mechanisms:
- `delegate_task` starts short-lived children inside the orchestrator's process.
- Separate profiles are long-running bots. Profiles have no built-in way to message each other, so they need a transport (webhooks, a file mailbox, a group room or Kanban).

According to our industry research, `delegate_task` children run inside the parent's profile and cannot be bound to another profile.

## Decision
v1 has one user-facing profile, `criba-hq`. It runs each role as a `delegate_task` child that loads that role's skill. The role "bots" are therefore skills run by children, not separate profiles.

## Alternatives considered
- One profile per role, with a transport between them: matches the brief's wording more literally, but needs a messaging layer and persistent bots before there is a case to justify them.
- Kanban (`kanban_create(assignee=<profile>)`): binds work to a profile, but is heavier and asynchronous across processes. Keep it as the upgrade path if a role ever needs its own profile (its own memory, secrets or channel).

## Consequences
- Children share no context with the orchestrator, so every input goes through the task envelope (0005).
- Child token usage is not tracked by Hermes. Workers self-report it in the result envelope.
- Moving a role to its own profile later is additive (0004 keeps the role logic in one place).
