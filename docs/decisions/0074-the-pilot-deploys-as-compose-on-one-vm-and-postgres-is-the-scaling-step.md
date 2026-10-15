# 0074. The pilot deploys as Compose on one VM; Postgres is the scaling step

- Status: Proposed
- Date: 2026-10-12
- Refines: 0022 (the stack and its deployment), 0028 (load mode names the next bottleneck), 0036 (sim mode stays out of production)
- Design: the pilot deployment design (proposed, not built)

## Context
The case bonus asks how we would deploy, monitor and scale the system. 0022 fixed the shape of a running stack (three containers, two named volumes, SQLite with one writer) but not where it runs, how it is backed up or upgraded, or when it outgrows one host.

The measured numbers set the size of the first deployment. The load run on the stub model held p95 0.529 s at 10,000 Candidates a week, ten times Grupo Sazón's volume, and Hermes was the first service to saturate, at about 300,000 a week (`docs/evidence/load-results.md:7-9`). The backend's p90 CPU stayed under a third of one core at that rate (`load-results.md:99`). Real-model latency and cost come from OpenRouter, not from our hosts (`docs/evidence/model-comparison.md:45-47`).

Two limits tie the backend to one process: SQLite has one writer (0022), and the turn lock that keeps one run at a time per Application is in process memory (`src/backend/criba/channel/service.py:94-96`).

## Decision
- **The pilot runs the existing `docker-compose.yml` on one VM**, with a TLS-terminating reverse proxy in front of `web`, the only published service. No orchestration layer.
- **Production never loads `docker-compose.sim.yml`.** `check_sim_settings` (0036) stays the guard, and the production profile and `CRIBA_ENV` are set explicitly.
- **The SQLite volume is backed up with SQLite's online backup API** (`sqlite3 .backup` or Python's `Connection.backup`) on a timer, copied off the host, and restored at least once before go-live. Continuous WAL shipping (Litestream) is the next step if the pilot needs a recovery point under the backup interval.
- **Images are built once per commit, tagged with the commit and deployed by digest**, as Hermes already is.
- **The scaling steps are ordered, each with a trigger** (the deployment design, Scale): more Hermes run concurrency, then more Hermes instances with sticky routing on the session id; Postgres together with a shared turn lock when the backend needs a second replica; a run queue in place of in-process background tasks; then a managed container service.

## Alternatives considered
- **A managed container service from day one.** It buys rolling deploys and managed secrets, but SQLite on a network volume with one writer gives up most of that, and the measured load does not need it.
- **Postgres now.** 0022 rejected it for the volume, and nothing measured since changes that: the bottleneck is Hermes, not the database.
- **File copies of `criba.db` as the backup.** A copy taken while the backend writes in WAL mode can be inconsistent; the online backup API is not.

## Consequences
- The pilot has one host, so a host failure is downtime until a restore. Owed turns (0073) survive a restart but not the loss of the volume.
- Every proposed item in the deployment design is marked as proposed until it is built; none of it is built yet.
- A second backend replica needs a new ADR for Postgres and the shared lock, as 0022 already says.
