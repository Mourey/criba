# 0019. Usage and cost ledger: product economics for Criba, results for the client

- Status: Accepted. Spike 1 (2026-10-09) confirms the Runs API returns tokens, including cache reads and writes, and the provider and model that served the run, but **no cost figure**. Cost is computed from OpenRouter prices and reconciled daily, as designed. Hermes's auxiliary calls must be pinned in config so they are deliberate and show up in reconciliation. Estimated cost for Haiku 5.5 with a lean toolset: about $0.002–0.004 per screening. See spike 1 (the Runs API).
- Date: 2026-10-09

## Context
The model provider is OpenRouter, through Hermes. The owner needs to track cost, charges, usage and tokens, to review how the product performs financially and technically. The client needs to see results. Those are two audiences with different views.

## Decision
- **A usage ledger in our store.** Each model call that serves a run appends one row: `ts`, `client`, `location`, `applicant_id`, `case_stage`, `profile` (agent), `run_id`, `session_id`, `kind` (run, subagent or auxiliary), `provider`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `est_cost_usd` and `latency_ms`. Its sources:
  - **runs:** `usage` and `runtime` from the Runs API (0018);
  - **sub-agents:** `subagent.complete` events (tokens and cost);
  - **cost:** estimated from OpenRouter's per-model prices, then **reconciled daily** against OpenRouter's own billing. The reconciled figure is the one that counts.
- **One OpenRouter API key per client and environment** (for example `sazon-prod`, `sazon-sim`). OpenRouter's own usage is then split along the same lines as ours, and simulation spend never mixes with production. Whether per-key spend caps are available is to be verified.
- **Two metric sets from the same data:**
  - **Criba (financial and technical):**
    - cost per conversation, per qualified candidate and per hire;
    - tokens per turn and the cache hit rate;
    - model mix and fallback rate;
    - latency at p50 and p95;
    - errors and retries;
    - margin against what the client is charged;
    - simulation and test spend.
  - **Client (results):**
    - funnel by stage, and time to qualify;
    - knockouts by reason;
    - drop-off and stalled candidates;
    - human takeovers and review requests;
    - all of the above by location and by country.
- **Access is separated.** Client users never see cost, token or margin data.

## Alternatives considered
- **Rely on OpenRouter's dashboard alone:** no breakdown by applicant, stage or agent, and nothing for the client.
- **Read Hermes's session database directly:** internal schema, not a contract. The Runs API fields are documented for cost attribution.

## Consequences
- Hermes's auxiliary calls (titles, compression, background review) may not appear in run usage. The daily reconciliation shows the gap, which is booked as overhead until it can be attributed.
- Cost per qualified candidate becomes the headline unit-economics figure, set against today's recruiter cost per qualified conversation (case part 1).
