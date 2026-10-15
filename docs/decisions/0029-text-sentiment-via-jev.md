# 0029. Sentiment from message text through Jev, to adjust tone and raise Escalations, never from voice and never in decisions

- Status: Accepted, amended by 0051
- Date: 2026-10-09
- Amends: the "no emotion inference" rule (0012, Phase 1 §2), and narrows 0007 (Jev is now used, for sentiment rather than routing)

## Context
The bonus list includes sentiment analysis: detect frustrated or confused Candidates and adjust tone. Our rules forbade emotion inference. As we understand the EU AI Act, an *emotion recognition system* works from **biometric** data (Art. 3(39)) and is **prohibited** in the workplace (Art. 5(1)(f)). The Commission's guidance includes recruitment in that. Text is not biometric data; voice is. The owner chose Jev (TypeSafe System One), which gives calibrated, typed judgements on text.

## Decision
- **For each inbound text message**, the backend asks Jev a small set of typed questions **in parallel with the agent run**:
  - is the Candidate frustrated?
  - are they confused about what is being asked?
  - do they want a human?
  - is the message off-topic or abusive?
- **The answers act only on the current turn:**
  - a tone hint goes into the agent's run (simpler wording, an apology, an offer of help);
  - above a threshold, an **Escalation** is raised.
- **Never stored as a property of the Candidate.** Never shown as a score about the person, and never an input to Knockouts, Rejections or any decision. Events record only *that* a tone hint or Escalation fired, and on which turn.
- **Never inferred from voice audio.** In voice sessions, Jev sees only the transcribed text (see the voice ADR).
- **Thresholds are calibrated** on simulator personas, using the `typesafe-jev-workflows` approach, and reported on the economics dashboard (Jev cost and latency per turn).

## Alternatives considered
- **Asking the screening model to judge sentiment itself:** uncalibrated, and mixed into the prompt that drives the conversation.
- **Skipping sentiment:** loses a bonus that can be done within the law.

## Consequences
- `TYPESAFE_API_KEY` goes in the backend's secrets.
- If Jev is down, the turn goes ahead without a tone hint. Failure is never treated as "calm".
- The defence point: "we drew the line at biometric data and at decisions." Our reading of the AI Act needs confirmation from the client's legal team.
