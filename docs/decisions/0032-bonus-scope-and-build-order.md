# 0032. Bonus scope: every listed bonus, built in a fixed order

- Status: Accepted
- Date: 2026-10-09

## Context
The case lists optional bonus features. Most are already part of the design.

## Decision
**Claimed through the existing design:**
- multi-language with code-switching (Phase 1 §4.3);
- guardrails (code-validated tools, scope rules, Escalation; ADR 0026);
- analytics (results and economics dashboards; 0019);
- re-engagement (the Stalled policy);
- tests and evals (simulator, load mode, model comparison; 0017, 0028);
- deployment design (the `deployment` perspective; 0022);
- sentiment (0029);
- interview scheduling (0030).

**Added:**
- an **ATS integration spec**, design only, as an **OpenAPI 3.1** file with `paths` for the calls we make to an ATS and `webhooks` for events an ATS sends us;
- **RAG over the FAQ knowledge base**, for the FAQ specialist.

**Build order:**
1. The core of Phase 2 (0023).
2. Approval and booking (0030).
3. Sentiment (0029).
4. The ATS spec.
5. Voice (0031).
6. RAG over the FAQ, at the very end.

## Alternatives considered
- **Choosing a subset:** most bonuses cost nothing extra given the design. Leaving them out would waste evidence.

## Consequences
- Until RAG lands, the FAQ specialist answers from a small set of approved answers, and raises an Escalation when it has none.
