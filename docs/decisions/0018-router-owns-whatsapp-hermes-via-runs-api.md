# 0018. Our router owns the WhatsApp channel and calls Hermes through the Runs API

- Status: Accepted. Spike 1 (2026-10-09, Hermes 0.21.5):
  - **PASS:** session continuity, idempotency (replay, and `409` on conflict), parallel runs across sessions, serialised runs within one session. p50 1.7 s per turn on Haiku 5.5 (first token 1.2 s; cold start 3.9 s).
  - **Not yet proven:** the per-profile `/p/<name>/` route needs that profile's own `API_SERVER_KEY` in its `.env`. A run through it was not completed.
  - **Caveats:** memory and `session_search` must be disabled, and `max_concurrent_runs` defaults to 10.
  - Spike 2 confirmed that the tool dispatcher's `session_id` equals the Runs API `session_id`, so identity flows from the run, never from the model.
  - See spike 1 (the Runs API) and spike 2 (tool exposure).
- Date: 2026-10-09
- Supersedes: 0015 on how WhatsApp connects (Hermes gateway). The rest of 0015 still holds.

## Context
Three new requirements need control over every message before it reaches an agent:
- a person must be able to take over a chat from the web panel and hand it back (0020);
- cost and usage must be attributed per applicant, client, location and agent (0019);
- simulated candidates must take exactly the same path as real ones (0017).

If Hermes's own WhatsApp gateway receives messages, they go straight to the agent's session, and our code can't pause it, re-route it, or attribute it.

The Hermes API server's Runs API covers what we need (`website/docs/user-guide/features/api-server.md:444-530`):
- `POST /v1/runs` with a `session_id` continues that session's transcript, and concurrent writers are serialised by session turn leases;
- `Idempotency-Key` makes retries safe;
- `GET /v1/runs/{id}` and the `run.completed` event return `usage` (input, output and cache tokens) and `runtime` (the provider and model that actually served the turn);
- `subagent.complete` events carry each child's tokens and cost.

Per-profile endpoints are served under `/p/<profile>/` (`multi-profile-gateways.md:399-440`).

## Decision
- **Our channel service owns the WhatsApp Cloud API webhook** and sends every outbound message. The router lives in the same service.
- **For each inbound message**, the service does three things:
  1. looks up or creates the applicant (0014);
  2. checks whether the conversation is with an agent or a human (0020);
  3. if with an agent, calls `POST /p/<role-profile>/v1/runs`, where `session_id` is the applicant's stable session key and the `Idempotency-Key` is the WhatsApp message ID. It then sends the run's output back through the Cloud API.
- **Every run's `usage` and `runtime` go into the usage ledger** (0019), tagged with client, location, applicant, profile and stage.
- **The simulator sends to the same service**, through a test adapter in place of the WhatsApp webhook.

## Alternatives considered
- **Hermes WhatsApp gateway plus a `pre_gateway_dispatch` hook to divert chats a person has taken over:** keeps more inside Hermes, but puts takeover, attribution and simulation into a plugin that runs inside the gateway, which is harder to test and less documented.
- **Let Hermes send the replies:** we would lose the single point where every outbound message is logged, rate-limited and checked against templates.

## Consequences
- We operate a small always-on service (webhook, router, sender). The Hermes gateway is used only for its API server.
- Spike 1 now has a concrete shape: prove that session continuity, idempotency and usage work through `/p/<profile>/v1/runs`, and measure latency against the 5-second target.
- Files such as `.ics` bookings are sent by our service as WhatsApp documents, from paths the agent returns.
