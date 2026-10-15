# 0060. A web chat conversation records its origin, and the Operator list reads it

- Status: Accepted
- Date: 2026-10-11
- Refines: 0035

## Context
Test chat keeps one conversation per browser tab, so an Operator cannot go back to an earlier test or demo conversation. A web chat conversation is a `candidate_identities` row (`channel = 'webchat'`, `external_id` = the conversation id); there is no conversations table. The start request's `client` (`test_chat`, `simulator`, `demo`) was validated and dropped, and the server never learned the Demo stage's persona, which lived only in the browser tab. Nothing could tell a Test chat conversation from a simulator run, and no endpoint listed conversations.

## Decision
- **Two nullable columns on `candidate_identities`** (migration 0009): `client`, one of `test_chat`, `simulator`, `demo`, and `persona_id`, 1 to 64 characters of `a-z`, `0-9` and `_`. Both hold codes only (ADR 0035): no persona name, no phone, no free text. The CHECKs in the table enforce it, and the start request refuses a `persona_id` that is not a code, or that comes without `client: "demo"`, with a 422.
- **Written once, at start.** `POST /api/chat/conversations` stores them on the identity row it writes, on the new-Candidate path (`repo.get_or_create_candidate`) and the existing-Candidate path alike. No web-chat response changes.
- **Old rows stay null and are not listed.** Conversations started before the migration cannot be told from simulator runs, and guessing an origin from a phone pattern or timing would mix simulator traffic into the list. The list starts with conversations created after the migration.
- **No separate retention.** The columns live and die with their identity row; nothing purges or erases them on their own.
- **An Operator-only list and read.** `GET /api/conversations` lists web chat conversations whose `client` is `test_chat` or `demo`, newest first, and `GET /api/conversations/{conversation_id}` reads one with its messages. A Recruiter gets 403, an anonymous caller 401, and an unknown id, or one whose `client` is null or `simulator`, 404. Both are read-only.
- **Conversation ids are bearer secrets.** On the public web chat API the id is the only credential. These endpoints hand it only to Operators, in JSON bodies and, for the read, in the request path. The new module logs nothing and a test checks that the app's records carry no id; the server's access log records the read's path, as it already does for `/api/chat/conversations/{id}`, and the shared route wrapper logs the request path on an unhandled error. We accept both.
- **Messages are per Candidate, not per conversation.** Two conversations started with the same phone share one Candidate, so both reads return the same web chat messages. The list marks such conversations with `shared_phone`. Splitting messages per conversation would need a `conversation_id` on `messages`, a larger change than the list asks for.

## Alternatives considered
- **A `conversations` table:** the identity row already is the conversation, written once at start; a second table would duplicate it.
- **Backfilling `client` on old rows:** a guess, and it would mix simulator runs into the list.
- **A `conversation_id` on `messages`:** the right fix for shared phones, but a migration over every message and a change to every writer; out of scope here.
- **Taking the persona from a Demo stage cookie or an authenticated call:** the start request is public by design, so the persona id is a pattern-checked display label that nothing trusts.

## Consequences
- The Operator list shows Test chat and Demo conversations created after migration 0009, and only to Operators.
- Anyone can start a conversation labelled with any persona id (the start request is unauthenticated); the label is for Operators to read and gates nothing.
- Conversations whose Candidate has a second web chat identity show each other's messages until messages are split per conversation.
- `contracts/schema.sql` holds the `candidate_identities` text as SQLite stores it after the ALTER, as 0008 did for `messages`.
