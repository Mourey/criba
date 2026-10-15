# 0022. Stack: a Python backend, a React frontend, SQLite, everything in Docker

- Status: Accepted. The role of Node is resolved by 0024 (build toolchain only).
- Date: 2026-10-09
- Case: Grupo Sazón, Phase 2 (the case brief)

## Context
Phase 2 requires Python for the backend. The owner has fixed the remaining stack: Hermes and OpenRouter for the agents (ADRs 0003, 0018, 0019), React for the web app, SQLite for the database, and Docker for everything, from the Hermes agent to the web app for Recruiters and Operators.

## Decision
- **Backend: Python only.** That covers the channel service (WhatsApp webhook, router, sender, scheduler; ADR 0018), the web app's API, validating answers, the usage ledger, and any tools the agents call.
- **Agents:** Hermes from its official Docker image (`website/docs/user-guide/docker.md`). The API server is enabled (`API_SERVER_ENABLED`, `API_SERVER_HOST=0.0.0.0`, `API_SERVER_KEY`) and reachable only on the internal Docker network. Models come from OpenRouter.
- **Frontend: React**, for the Recruiter and Operator web app (ADRs 0015, 0020).
- **Database: SQLite**, holding Candidates, Applications, messages, events and the usage ledger. It sits on a Docker volume, in WAL mode, and the Python backend is its only writer. Hermes keeps its own `state.db` separately; we never read it directly (ADR 0019).
- **Deployment:** Docker Compose, with one container each for Hermes, the Python backend and the web app, plus named volumes for SQLite and the Hermes home.

## Alternatives considered
- **Postgres:** more capacity for concurrent writers than we need. About 1,000 applications a week means a few thousand messages a day, and with a single writing process SQLite handles that comfortably.
- **A Node backend for the web app:** ruled out by the requirement that the backend be Python.

## Consequences
- SQLite's single-writer model means all writes go through one Python service. That service exposes everything the web app and the agents need.
- Backups are file-level, using SQLite's online backup.
- If we later scale to several backend replicas, we would move to Postgres under a new ADR.
