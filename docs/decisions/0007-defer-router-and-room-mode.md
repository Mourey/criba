# 0007. Defer the Jev router and Bot Mode rooms

- Status: Accepted, narrowed by 0009. A code-rule router is now in v1; the Jev classifier and Bot Mode rooms stay deferred.
- Date: 2026-10-09

## Context
The sketch routes every request through a Jev (TypeSafe) battery and escalates multi-domain deliberation to a Bot Mode group room. With no case, there are no roles to route between and no fixtures to calibrate against.

## Decision
In v1 the orchestrator plans the tasks itself, guided by its skill. There is no router and no room mode.

## Alternatives considered
- Build the router now: its question battery and thresholds would be fitted to placeholder roles.

## Consequences
- Revisit with a new record when any of these happens: the case has 3 or more roles, misrouting shows up in runs, or a task needs agents to argue over each other's conclusions.
- The sketch's §4 (router) and §5.2 (room mode) are the starting designs if that happens.
