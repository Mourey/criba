# 0036. Sim mode is one Compose overlay, and the backend refuses a mismatch at startup

- Status: Accepted
- Date: 2026-10-09
- Refines: 0017 and 0028 (how the simulator runs against the real stack)

## Context
The simulator (0017, 0028) runs personas against the real backend and Hermes with a stub model instead of a real one. Three settings must change together: the stub model server must run, the backend must call the sim Hermes profile (whose model is the stub), and `CRIBA_ENV=sim` must turn on the test-only clock endpoints. If only some change, either sim traffic reaches a paid model, or production traffic runs on the stub with a movable clock. Settled before wave 2 as Q39.

## Decision
- **One overlay turns sim mode on:** `docker-compose.sim.yml` adds the `stub-llm` service, sets `CRIBA_ENV=sim` and `CRIBA_HERMES_PROFILE=criba-recruiter-sim` on the backend, and makes Hermes wait for the stub. Run it as `docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d`.
- **The backend checks the pair at startup.** `check_sim_settings` (`src/backend/criba/api/sim.py`) raises when `CRIBA_ENV=sim` is set without a `-sim` profile, or a `-sim` profile is set without `CRIBA_ENV=sim`. The app calls it before migrating.
- In sim mode the summary is deterministic and no OpenRouter key is written to the sim profile (`src/docker/hermes/entrypoint.sh`).

## Alternatives considered
- **Separate switches, documented:** relies on people setting three variables right every time.
- **A sim build of the backend image:** a second image to keep in step, for a difference of two variables.

## Consequences
- The four combinations are unit-tested; the overlay file says it must never be used in production.
- The end-to-end tests in `src/tests/e2e/` run against a stack started with this overlay.
