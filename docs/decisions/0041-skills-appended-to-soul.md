# 0041. Skills reach the client-facing agent through `SOUL.md`

- Status: Accepted
- Date: 2026-10-09
- Refines: 0011 (specialists "loaded inline with `skill_view`") and 0004 (roles as skills)

## Context
0011 says specialists are skills, loaded inline with `skill_view` for quick steps. Client-facing profiles run a lean toolset (spikes 1–2: about 7k input tokens per turn instead of about 40k), and it has no skill tools: `skill_view` is not there, and `skill_manage` would let the model write to a skill. The Screener skill therefore had no way to reach the agent. Found and solved in lane W1-C.

## Decision
- **Each profile lists its skills in `skills.txt`.** At container start the Hermes entrypoint (`src/docker/hermes/entrypoint.sh`) appends each listed skill's `SKILL.md`, minus its frontmatter, to the profile's `SOUL.md`, after the identity.
- **A listed skill that is missing stops the entrypoint**, so a profile never starts without its instructions.
- **The skill directory is installed too**, for later use by `delegate_task` children (0011's delegated specialists).
- No skill tools are added to client-facing profiles.

## Alternatives considered
- **Add `skill_view` to the lean toolset:** one more tool schema on every turn and an extra round trip before the first answer, for a skill the agent always needs.
- **Copy the skill text into `SOUL.md` by hand:** two copies that drift; the skill stays the single source.

## Consequences
- For the client-facing layer, "loaded inline" in 0011 means appended to `SOUL.md` at start, not loaded on demand. Delegated specialists are unchanged.
- Skills are baked into the Hermes image, so a skill change takes effect after a rebuild and restart.
- Every listed skill costs input tokens on every turn, so a profile lists only the skills its agent always needs.
- `src/backend/tests/hermes/test_profiles.py` checks the profile files and the skills they list.
