# 0020. A person can review a case and take over the chat from the web panel

- Status: Accepted
- Date: 2026-10-09

## Context
Some loops need a person: knockout reviews (0016), significant decisions (0012), confused or upset candidates, and edge cases. A person needs to see the evidence so far and step into the conversation without leaving the panel.

## Decision
- **Applications view.** It shows who the applicant is, their stage, where they stopped, the time spent on each step and in total, the source and location, and flags (review requested, stalled, takeover active). It can be filtered and sorted.
- **Case view, the evidence.** It shows the full chat transcript, the fields collected with their checks, the event timeline (knockouts, nudges, decisions), and any files delivered. Criba users also see the case's cost and tokens.
- **Take over.** Each case record has a `control` field, either `agent` or `human:<user>`. While a person holds control:
  - the router stops creating agent runs for that applicant;
  - inbound messages go only to the panel;
  - the person's replies are sent through our WhatsApp service and logged on the case record.

  The candidate is told that a person has joined, the counterpart to the AI disclosure.
- **Hand back.** The person returns control. The next agent run receives a short summary of what was said during the takeover, so the agent continues from the right place. Takeover and hand-back are both case events.
- **Queues.** Review requests and pending decisions appear as a work queue in the same panel.

## Alternatives considered
- **Take over inside the Hermes Desktop app:** client recruiters would need Hermes installed, and it doesn't know about case records.
- **Notify a person, who replies from the WhatsApp Business app:** the agent and the person would both answer, nothing would be logged on the case record, and there would be no clean hand-back.

## Consequences
- Possible because our service owns the channel (0018).
- Takeovers count toward the client's metrics, and their frequency shows where agents fall short.
- Two people taking over the same case at once is prevented by a lock on `control`.
