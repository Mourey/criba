# 0065. ElevenLabs Agents listens and speaks, and our backend is its custom LLM

- Status: Accepted
- Date: 2026-10-11
- Supersedes in part: 0031 (vendor and transport)
- Refines: 0051 (on spoken turns Jev runs after the reply and decides no language)

## Context
ADR 0031 chose a pipeline of OpenAI transcription and text-to-speech around our backend, and listed "ElevenLabs with our model as its custom LLM" as an alternative not taken. The owner then chose ElevenLabs Agents, and the voice spec (R1–R26, amended through decision 51 of the voice agent log) and its children built it; they are merged on `main`. ADR 0059 later made voice its own flow (channel, Hermes profile, Screener skill) but does not record the vendor, the transport or what Jev does on a spoken turn. This record does, for decisions the spec and the log already made. The voice specs call it "ADR 0057" and log decision 55 "0058"; both numbers went to other records. Log decision 66 said to record ADR 0059 instead of a new ADR; the orchestrator's ruling on it, made because 0059 never names the vendor or Jev on spoken turns, gives this record 0065.

## Decision
- **ElevenLabs Agents does the listening, speaking, turn-taking and filler phrases; our backend is its custom LLM.** One ElevenLabs agent per country calls `POST /api/voice/v1/chat/completions` (`contracts/voice.openapi.yaml`; `src/backend/criba/channel/voice.py:1`), which runs the turn through our channel service and Hermes, as ADR 0059 routes it. Filler phrases are the agent's soft-timeout messages (`FILLERS` and `soft_timeout_config`, `src/backend/criba/channel/voice_agents_config.py:37`, `:153-158`).
- **What 0031 keeps.** No audio is stored (`"record_voice": False`, `voice_agents_config.py:172`; the post-call webhook body is never logged or stored, `voice_webhook.py:19`), and nothing reads emotion from the voice: Jev reads the transcript text only. 0031's "channel adapter of the chat" line is superseded by 0059, not by this record.
- **Binding.** ElevenLabs sends the agent's custom-LLM secret (`criba_voice_llm_secret`) as `Authorization: Bearer`, and our signed voice token as `elevenlabs_extra_body.criba_voice_token` on every request (spike 005 rows 1 and 3, the probe's write-up; `voice.py:7-12`). Ids come from the verified token only, never from the request body.
- **One message per utterance.** The message id is `vc_<voice_session_id>_<n>`, where `n` counts the `user` entries in the history; it is the idempotency key, so a request ElevenLabs re-sends with the same history replays the stored turn (spike 005 rows 2 and 4; `voice.py:371`).
- **Streaming.** The reply streams as `chat.completion.chunk` frames; Hermes has no end-of-message event, so `[DONE]` follows `run.completed`. When nothing is written within `FIRST_CHUNK_S` (2 s), a `"... "` first chunk goes out so ElevenLabs does not re-send (log decision 33; `voice.py:58-59`).
- **Conversation id.** The ElevenLabs conversation id is written once when the voice-session endpoint mints the token, and the post-call webhook joins on it.
- **Jev on spoken turns (refines 0051).** A spoken turn reads Jev after its reply is stored, not before the run; it decides no language (`decide_language=False`), and its Tone hint is keyed on the reply so it applies to the Candidate's next turn. The usage row, the Escalation path and the message signals are as on a typed turn. Jev is still never an input to a Knockout, Rejection or Approval.
- **A Takeover ends the call.** A spoken request on a session a Takeover ended gets the spoken handover line, then `end_call` with `{"reason":"takeover","criba_end_reason":"takeover"}`; ElevenLabs ends the call only when `reason` is present (voice spec R16; log decisions 11, 20, 34; `voice.py:65`, `voice.py:351-352`). The browser cannot tell a Takeover end from any other agent-side end, so the page shows "ended" for both.
- **Agent configuration is code.** `src/scripts/voice_agents.py` writes and checks each country's agent from `voice_agents_config.py`: voice A of spike 004's shortlist per country until the panel rates (`voice_agents_config.py:29-33`), a post-call webhook set per agent, not per workspace (log decision 40), and ElevenLabs' platform defaults accepted where they differ. The backend refuses a voice session while the live agent drifts from it.

## Alternatives considered
- **OpenAI transcription and text-to-speech around our backend** (0031): we would own turn-taking, barge-in and fillers in the browser; the voice intent, amended after spikes 3 and 4 (the Spanish voice spike), settled on ElevenLabs Agents for those (voice spec, "Problem restated").
- **Speech-to-speech** (0031's own fallback; OpenAI Realtime): it cannot run our own model or agent, only call our tools, so the screening procedure, the validators and the record would sit behind someone else's model.

## Consequences
- Speech is a cost of its own: the post-call webhook writes one `speech` usage row per call, with its audio seconds and estimated cost.
- Four secrets: `elevenlabs_api_key`, `criba_voice_secret` (the voice token), `criba_voice_llm_secret` (the custom-LLM bearer) and `elevenlabs_webhook_secret`, which ElevenLabs issues.
- A live call needs a public HTTPS URL for the custom-LLM endpoint (a tunnel in the demo); the sim stack replaces ElevenLabs with the voice driver and the stub model.
- Earlier merges describe voice as a mode of the web chat; ADR 0059 replaced that flow, and everything above holds on the voice flow.
