# 0017. Test with simulated candidates and monitor the chats

- Status: Accepted
- Date: 2026-10-09

## Context
The system is client-facing and conversational, and it runs on WhatsApp. We need to test it without real candidates or a live WhatsApp number, and to watch what agents actually say.

## Decision
- **A simulation harness** plays candidates. Personas are defined as data (qualified, no license, outside the zone, goes silent partway, answers out of order, writes Mexican or Spain Spanish, tries to manipulate the bot). Each persona gets its own simulated phone number and talks to the recruiter profile through a test channel, never through WhatsApp.
- **It simulates both entry paths:** the candidate writing first, and our outreach to imported applicants. It can also simulate time passing, to exercise the stalled-candidate policy.
- **Assertions check the case record, not the chat prose:**
  - the final stage;
  - fields collected in the required order;
  - knockout reason, message template and link;
  - the AI disclosure is present;
  - latency per reply.
- **Chat monitoring:** the web app's chat view shows simulated and real conversations the same way, so a person can read runs.

## Alternatives considered
- **Manual testing on a WhatsApp test number:** slow, can't be repeated, and can't simulate time.

## Consequences
- The test channel has to feed the same session and router path as WhatsApp. Whether that means the Hermes API server with one session per simulated number, or a fake platform adapter, is decided in the first spike.
- The persona fixtures double as the regression suite when prompts or skills change.
