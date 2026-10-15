# 0023. Phase 2 scope: screening agent and Recruiter tracker, plus the tools to defend them

- Status: Accepted
- Date: 2026-10-09
- Case: Grupo Sazón, Phases 2 and 3

## Context
Phase 2 asks for two things: a conversational agent that screens Candidates, and a tool for Recruiters to keep track of them. Our design (ADRs 0008–0021) is larger, and every choice needs measured evidence.

## Decision
**In Phase 2:**
- the Screening Agent: the Phase 1 flow, Knockouts with Review, Stalled nudges, Escalation;
- the Recruiter tracker: applications view, Application detail, Claim, review queue;
- Takeover;
- the simulator, including load mode (0028);
- the usage ledger, the economics dashboard UI, and a minimal results view (funnel by Outcome).

**Out of Phase 2** (kept in their ADRs, listed in the README's Potential improvements):
- `criba-hq` and HQ chat (the internal layer, 0008);
- outreach to imported Candidates (0014);
- onboarding and insights agents;
- the recruiter specialists for job descriptions, scheduling and no-show reminders (0011).

## Alternatives considered
- **Only the two stated items:** leaves the defence without evidence for the model and cost choices, or for scale.
- **The whole design:** the internal layer and the other agents add a lot of surface area and nothing a Recruiter needs on day one.

## Consequences
- Excluded items need no redesign later; they just aren't built.
- The economics dashboard is on the Phase 2 critical path, so the ledger (0019) must capture data from the first simulator run.
