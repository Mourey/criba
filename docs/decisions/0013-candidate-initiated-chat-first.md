# 0013. The candidate writes first; reaching out to ingested applicants comes second

- Status: Accepted, amended by 0014 (applicants needn't be in the database already; we own it)
- Date: 2026-10-09
- Case: Grupo Sazón, part 1 (the case brief)

## Context
Grupo Sazón gets about 1,000 applications a week, and 60% of candidates don't answer screening calls. Lead generation and the application forms are out of scope: applicants come from elsewhere, and we only ingest those in a database. The owner wants speed: a candidate who sees they can talk to us at once will click, and we must process them as fast as possible.

## Decision
- **Main entry: the candidate writes first.** A link on the website opens a chat. The candidate's first message opens their session on the recruiter profile, and the router creates or matches their case record.
- **Second entry: we reach out to applicants already in the database.** The router starts the conversation for applicants who haven't written yet.
- **Screening runs inline in the conversation.** The role orchestrator asks the knock-out questions and checks the answers itself, loading the screener skill with `skill_view`. The fast path has no `delegate_task`, so a reply is one model turn. Delegation is kept for slow work outside the conversation (ADR 0011).
- **The link carries a source tag** (for example, a pre-filled first message with a location or campaign code), so the router knows which of the 45 locations or which campaign brought the candidate.

## Alternatives considered
- **We reach out first, by calling:** this is today's process, and it is what gives the 60% no-answer rate.
- **We reach out first, by message, to everyone in the database:** keep it as the second path. On WhatsApp, a message the business starts outside the 24-hour window after the user's last message must use a template Meta has approved in advance. When the candidate writes first, that window opens and free-form conversation is allowed.

## Consequences
- Spike 1 (opening a session per person from code) narrows. Hermes's gateway already keeps one session per chat for inbound messages. What is left to test is starting a conversation from code for the outbound path, including WhatsApp templates if WhatsApp is the channel.
- Spike 2 (replying while specialists run) moves off the fast path. Screening needs no background children.
- A latency target per reply should be set and measured. Proposal: the first reply within 5 seconds of the candidate's first message.
