# 0031. Voice is a pipeline channel in front of the same Screening Agent, using OpenAI speech; it is built last

- Status: Accepted, superseded in part by 0059 (voice is its own flow, not a channel adapter of the chat; one Application no longer mixes voice and chat) and 0065 (vendor and transport: ElevenLabs Agents with our backend as custom LLM). The fallback to speech-to-speech depends on a latency spike.
- Date: 2026-10-09

## Context
The "great" bonus is a browser-based voice agent that runs the same screening. The owner chose OpenAI and named the "OpenAI Voice One API". Research found no product by that name. The closest are the Realtime API's `gpt-realtime-2.1` (speech-to-speech) and OpenAI's speech-to-text and text-to-speech models. Speech-to-speech can't use our own model or agent; it only calls our tools. Reports of weak Spanish recognition and language switching are forum-grade and need testing.

## Decision
- **A pipeline:** browser audio, then OpenAI transcription, then **our backend and the same Screening Agent** (Haiku 5.5, the same tools, Jev on the transcript), then OpenAI text-to-speech, back to the browser.
- **Transport:** WebRTC, using short-lived tokens minted by the backend; the API key never reaches the browser.
- **Voice is a channel adapter** (ADR 0018). The Application, tools, Knockouts, Takeover and ledger are unchanged, and one Application can mix voice and chat.
- **Filler phrases** cover the model's time on slow turns.
- **No audio is stored**; only the transcript is. Zero data retention is requested from OpenAI for the organisation. Emotion is never inferred from audio (ADR 0029). The agent and Jev only ever see text.
- **Build order:** voice comes after everything else in Phase 2, except RAG over the FAQ, which is last.

## Alternatives considered
- **Speech-to-speech with `gpt-realtime-2.1` as a separate voice agent:** the most natural latency, but a second prompt on a second model family, and the line on biometric data would rest on instructions alone. This is the fallback if a spike shows the pipeline is too slow, under a new ADR.
- **ElevenLabs with our model as its custom LLM:** fits the architecture, but isn't the owner's choice.

## Consequences
- The usage ledger gains OpenAI speech rows (audio minutes and tokens), alongside OpenRouter.
- Spanish recognition and language switching are tested with simulator voice personas before the demo.
