# 0025. Models: Claude Haiku 5.5 for screening turns, Claude Sonnet 5.5 for summaries and Escalation decisions

- Status: Accepted, to be confirmed by the model comparison run (0028)
- Date: 2026-10-09

## Context
The case asks for a justified choice of LLM. Each screening turn needs speed (first reply within 5 seconds), good Spanish (es-ES, es-MX) and English, and reliable tool calls. The volume is about 1,000 conversations a week, with 10,000 as the scaling target. Answers are validated by code (0026), so the model drives the conversation rather than judging the answers.

## Decision
- **Screening turns:** Claude Haiku 5.5 (`claude-haiku-5-5`), through OpenRouter.
- **The post-conversation summary and Escalation decisions:** Claude Sonnet 5.5 (`claude-sonnet-5-5`). There is one call per Application, where quality is worth the price.
- **Fallback:** OpenRouter's automatic fallback moves the turn to Sonnet 5.5 if Haiku errors. The ledger records the model that actually served each run (0019).
- **The justification is measured.** Simulator personas run against both models, and the economics dashboard reports latency at p50 and p95, cost per Qualified Application, the rate of answers failing validation, and the Escalation rate.

## Alternatives considered
- **Sonnet 5.5 for every turn:** better judgement on ambiguous answers, but slower and more expensive per turn, and code validates the answers anyway.
- **A non-Anthropic model:** possible through OpenRouter, and can be added to the comparison run with nothing but configuration.

## Consequences
- If the comparison shows Haiku failing on code-switching or ambiguous Zones, the turn model changes under a new ADR, and the dashboard shows why.
