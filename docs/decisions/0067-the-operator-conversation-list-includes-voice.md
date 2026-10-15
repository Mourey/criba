# 0067. The Operator conversation list includes voice conversations, each with its channel

- Status: Accepted
- Date: 2026-10-12
- Refines: 0060, 0059

## Context
ADR 0060 made the Operator list read `candidate_identities` rows of `channel = 'webchat'` whose `client` is `test_chat` or `demo`. Voice is its own flow (ADR 0059): the Voice page starts a conversation with `channel: voice`, which writes an identity row of `channel = 'voice'` and a `wv_` id, with the same `client` codes. The list filtered on `webchat`, so no voice conversation ever appeared, and an Operator could not read what a Candidate said on a call. Transcript turns are stored as `messages` with `channel = 'voice'` and `modality = 'spoken'`; no audio is stored (ADR 0065).

## Decision
- **The list and the read cover `webchat` and `voice` identities** with a recorded `client` of `test_chat` or `demo`. Simulator runs, rows from before the origin was recorded and WhatsApp identities stay out. The read answers 404 for any other id, as before.
- **`Conversation.channel`** (`webchat` or `voice`, required) is added to `getConversation` and `listConversations`, so an Operator can tell a spoken conversation from a typed one. It is a code, never free text (ADR 0035). This is a contract change to `contracts/api.openapi.yaml`, owned by the web app and REST API lane, with `schema.d.ts` regenerated.
- **Everything about a conversation is read on its own channel.** The messages, `last_message_at`, the Application of the latest message and `shared_phone` (more than one identity of the same channel on the Candidate) use the identity's channel. A Candidate with a typed and a spoken conversation therefore does not see one mixed into the other. The detail view of a voice conversation shows the spoken transcript (each message carries `modality: spoken`).
- **Nothing else changes.** Operator-only, read-only, ids are bearer secrets and are not logged (ADR 0060). No migration: the identity row already holds the channel.

## Alternatives considered
- **A separate `/api/voice-conversations` list:** two lists for one Operator task, and a second contract surface; one list with a `channel` field is simpler.
- **Keeping messages unfiltered by channel:** a typed message would show in a call's transcript.

## Consequences
- Voice conversations started from the Voice page with `client: test_chat` appear in the list, newest first with the web chat ones.
- Clients of `listConversations` and `getConversation` receive one more required field.
