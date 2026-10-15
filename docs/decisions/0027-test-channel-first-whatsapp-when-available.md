# 0027. Phase 2 runs on a web chat test channel; WhatsApp is switched on when a number exists

- Status: Accepted
- Date: 2026-10-09
- Amends: 0015 (the channel is WhatsApp in production)

## Context
Live WhatsApp needs a Meta Business account, a verified number and approved templates. That depends on Grupo Sazón and can take days. Recruiters should be able to use the product, and the defence needs a live demo, regardless.

## Decision
- The channel service has two inbound and outbound adapters (0018): **web chat**, a test chat page in the web app, and **WhatsApp Cloud API**.
- Phase 2 runs on web chat and the simulator. The WhatsApp adapter is built and tested against recorded webhook payloads, and enabled by configuration.
- Everything past the adapter is identical: router, Applications, tools, ledger and tracker.

## Alternatives considered
- **Block Phase 2 on live WhatsApp:** puts an external dependency on the critical path.

## Consequences
- The 24-hour window and template rules (0015) are enforced in the outbound sender, and exercised in tests through the WhatsApp adapter.
