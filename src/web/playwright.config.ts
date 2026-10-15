import { defineConfig, devices } from "@playwright/test";

// End-to-end tests (tests/e2e) run against a real stack, never mocks. CRIBA_E2E_BASE_URL (the
// stack's web URL) is the only source of the base URL; with it or CRIBA_E2E_SEED_PASSWORD_FILE (its
// seed password secret) unset every spec skips, the same convention as src/tests/e2e. The stack must
// be a sim stack (docker-compose.sim.yml), so the agent is the stub model and no real model is called:
// requireStack() fails the run unless GET /api/sim/clock answers 200.
// Browsers are not installed by default: run `npx playwright install chromium` first.
export default defineConfig({
  testDir: "./tests/e2e",
  // One stack, one sim clock: run the specs one at a time.
  workers: 1,
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.CRIBA_E2E_BASE_URL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
