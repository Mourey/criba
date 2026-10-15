# Constraints

Last reviewed: 2026-10-09 by Rodrigo Mourey

Criba's quality bar. This file is canonical: `src/scripts/check.sh` mirrors it, and if the two drift, this file wins. Every check runs in the dev container (`docker compose --profile dev up -d dev`), never on the host; `check.sh` re-runs itself there.

## Floor (always enforced, no setup required)

Checked by `node src/scripts/floor_guard.mjs` (diff against the merge base with `main`, plus untracked files). Blocks now.

- No new suppression comments: `# noqa`, `# type: ignore`, `# pragma: no cover`, `@ts-ignore`, `@ts-expect-error`, `eslint-disable`, `nosemgrep`, `gitleaks:allow`, unless the comment cites an exception below (`exception W1`)
- No unimplemented stubs: `raise NotImplementedError`, `except ...: pass`, empty `catch {}`, `TODO` or `FIXME` in code
- No skipped or deleted tests: `pytest.skip`, `@pytest.mark.skip`/`xfail`, `.skip`, a deleted test file, an assertion removed from a test that stayed
- An edited assertion is not a removed one: assertions are compared as whole statements (a multi-line `assert (...)` or `expect(...)` chain is read from its first line to its last), and a removed assertion pairs with an added one in the same test function or `it`/`test` block when both are about the same subject (the left-hand side of the comparison, or the first argument). A pair is reported as `assertion-changed` and does not fail; a removal left without a partner is still `assertion-removed`.
- A reformat is not an edit: a statement whose text is the same once whitespace, line wrapping, quote style, trailing commas, the parentheses round an `assert` condition or message, and the order of the two sides of a lone `==`/`!=` are ignored is unchanged, and the guard reports nothing for it.
- No secrets in source or history: `gitleaks git --redact` (blocks now)
- This file does not get weakened to make a change pass: a lowered threshold, a removed rule or a new exception is reported by the floor guard

Paths the guard skips are listed in `.constraintsignore`, each with its reason.

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at | Why |
|-----------|------|-----------|---------|-----|
| Lint (backend) | Zero errors, format clean | `ruff check src` (all Python under `src/`) + `ruff format --check src/backend src/tests src/scripts src/hermes` (2 config files: `src/backend/pyproject.toml` holds the settings, and `src/ruff.toml` extends it for `src/tests`, `src/scripts` and `src/hermes`) | every edit (changed file), task end | Already the CI gate; catches it before CI |
| Lint (web) | Zero errors | `eslint` (`src/web/eslint.config.js`) | every edit (changed file), task end | Same |
| Types (web) | Zero type errors | `tsc -b` | task end | Already part of `npm run build` |
| Tests | All pass | `pytest` (root `pytest.ini`), `vitest run`; at task end only the tests related to the changed files (`src/scripts/select_tests.py`: `pytest <mapped paths>`, `vitest related`), the full suites in CI | task end (related), CI (full) | Hermes-marked tests stay in their own run (README); related tests keep task end under 90 s as the suites grow, and anything the mapping cannot place (shared config, unmapped source) runs the full suite |
| Coverage (changed lines) | ≥ 80% of changed lines covered, backend and web together | `diff-cover` on `pytest-cov` and `@vitest/coverage-v8` Cobertura reports, from the tests that ran | task end, CI | High enough to force a test, low enough to allow a config line |
| Security: secrets | No findings | `gitleaks git --redact` (history) and `gitleaks git --pre-commit --redact` (uncommitted) | task end, CI | Candidate PII and Client credentials; redaction keeps values out of transcripts |
| Security: code | No new ERROR-severity finding against `main` | `semgrep scan --config p/default --severity ERROR --baseline-commit <merge base>` | CI | External opinion; the 9 findings on `main` were reviewed on 2026-10-09 (8 f-string SQL with constant or allowlisted identifiers, 1 Hermes image runs as root) |
| Security: dependencies | No known vulnerability | `osv-scanner scan source` on `uv.lock` and `package-lock.json` | CI | External opinion; zero today, so any finding is new |
| Contracts | Frozen contracts load and validate | `src/scripts/check_contracts.sh` | CI | Spec 005 gate |
| Generated web types | `src/web/src/api/schema.d.ts` and `chat-schema.d.ts` equal what `openapi-typescript@7.13.0` generates from the contracts | `web_types` in `check.sh full` (`git diff --no-index --exit-code`) | CI | The checked-in types cannot drift from the frozen contracts |
| Web build | Builds | `npm run build` | CI | What nginx serves |

Every row names the command that produces the verdict.

## Measured, not yet enforced

Ratchets: each must not get worse than the recorded value (coverage tolerance 0.5 points). When a number improves, update it here in its own commit; when it gets worse, that is the finding. The mypy ratchet is checked by `check.sh task` and `full`; the two coverage ratchets are project-wide, so they need the full suites and are checked by `check.sh full` (CI) only.

| Metric | Today | Direction |
|--------|-------|-----------|
| Backend line coverage | ≥ 96.7% | must not fall |
| Web line coverage | ≥ 92.6% | must not fall |
| Backend mypy errors | ≤ 0 | must not grow |

## Enforcement

| Check | Mode |
|-------|------|
| Floor, secrets, lint, `tsc`, tests, contracts, web build | block now |
| Coverage, mypy, Semgrep, osv-scanner | warn until 2026-10-23, block from then (`WARN_UNTIL` in `check.sh`) |

Placement: the edit loop (Claude Code PostToolUse hook in `.claude/settings.json` running `check.sh hook`) runs only lint on the changed file and the floor guard, under 5 s. `check.sh task` runs at task end, under 90 s: lint, `tsc`, the tests related to the changed files, changed-line coverage, the mypy ratchet and gitleaks. `check.sh full` runs in CI, unbounded: the same checks over the full test suites, plus the coverage ratchets, Semgrep, osv-scanner, the contract gate and the web build.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| W1 | Semgrep `dockerfile.security.missing-user` | `src/docker/dev/Dockerfile` | The dev container runs as root so bind-mounted files stay writable on Linux CI runners; it has no secrets and no service networks | Ramiro Salcedo | 2027-01-07 |
| W2 | gitleaks `generic-api-key` on the value `0123456789abcdef` (`.gitleaks.toml`) | `src/backend/tests/**` | Placeholder session secret in W2-D API tests (`tests/api/test_sim.py`), not a credential | Ramiro Salcedo | 2027-01-07 |
| W3 | `test-deleted`, `assertion-removed` | `src/web/tests/unit/slots-book.test.tsx` | Frontend redesign shell-app child: a pure move of this file into `slots.test.tsx` and `book.test.tsx`, so two parallel children never share a test file (the redesign's app-shell step). The two new paths were listed temporarily (575e992) while git's rename detection charged the moved assertions to them, and removed after shell-app merged (owner decision 2026-10-10) | Ramiro Salcedo | 2026-12-31 |
| W4 | `test-deleted`, `assertion-removed` | `src/web/tests/unit/dashboards.test.tsx` | Frontend redesign shell-app child: a pure move of this file into `economics.test.tsx` and `results.test.tsx`, so two parallel children never share a test file (the redesign's app-shell step). The two new paths were listed temporarily (575e992) while git's rename detection charged the moved assertions to them, and removed after shell-app merged (owner decision 2026-10-10) | Ramiro Salcedo | 2026-12-31 |
| W5 | `assertion-removed` | `src/web/tests/unit/operator-panel.test.tsx` | Muted-line amendment to JI9 (owner decision 2026-10-12): the case "shows the muted line once when only one of the two is missing" asserted the line exists, and the new rule asserts it is absent, so `getByText` becomes `queryByText` and the guard cannot pair them. Four assertions, `fix/intent-muted-line` | Ramiro Salcedo | 2026-10-31 |

A path-scoped row lifts only the floor guard findings its Rule column names (`assertion-removed`, `test-deleted`), only for files its Path globs match, and only until its Expires date (UTC); the guard prints each lifted finding with the exception id, and reports an expired row as `exception-expired`.
