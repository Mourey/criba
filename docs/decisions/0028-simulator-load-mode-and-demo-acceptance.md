# 0028. The simulator gains a load mode and a model comparison; the demo script is the acceptance test

- Status: Accepted
- Date: 2026-10-09
- Extends: 0017

## Context
The design has to answer how the system scales to 10,000 Candidates a week and why we chose this model, and to show it live. Rough load at 10,000 a week:
- about 1,400–2,000 Applications a day;
- about 16,000 inbound messages a day;
- about 0.7 messages a second at peak.

SQLite with one writer handles that. The likely limits are concurrent model calls, OpenRouter rate limits, how many concurrent sessions Hermes's API server can serve, and WhatsApp's sending limits.

## Decision
- **Load mode:** generated personas replay Candidates at a chosen weekly rate (1,000 and 10,000) on a faster clock. It runs against the real stack, using the `sazon-sim` OpenRouter key or a stub model. It reports latency, errors and the first bottleneck.
- **Model comparison:** the same persona set runs against each candidate model (0025). Results are saved as a dataset the economics dashboard replays.
- **The demo script is the Phase 2 acceptance test:**
  1. A simulated Candidate gets a Knockout, and another is Qualified, in the test chat.
  2. The Application appears in the tracker.
  3. A Recruiter claims it and takes over.
  4. The economics dashboard shows those runs.

## Alternatives considered
- **Arguing scale on slides alone:** weaker in front of an engineer.

## Consequences
- Load results and the next bottleneck, with its fix (Postgres, more Hermes replicas, queueing), go into `docs/evidence/`.
