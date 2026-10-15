// Operator flow against the real API: log in, find the Application, Claim it, take over the chat,
// write to the Candidate, hand back, then Approve it from the review queue.
import { expect, test } from "@playwright/test";
import { apiAs, applicationFor, Candidate, candidateName, logIn, OPERATOR, requireStack, screeningAnswers } from "./support.ts";
import { mkdirSync, statSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { PUEBLA_RECRUITER } from "./support.ts";

requireStack();

test("operator claims, takes over, writes, hands back and approves", async ({ page }) => {
  // A Candidate in Puebla screens to Qualified through the web chat (stub model).
  const name = candidateName();
  const candidate = await Candidate.start("MX");
  await candidate.say("Hola, quiero trabajar de repartidor");
  for (const answer of screeningAnswers()) await candidate.say(answer);
  const api = await apiAs(OPERATOR);
  const app = await applicationFor(api, candidate.phone, "qualified");

  // Log in and find the Application in the list.
  await logIn(page, OPERATOR);
  await page.getByRole("searchbox", { name: "Search Candidate" }).fill(candidate.phone);
  const row = page.getByRole("row").filter({ hasText: candidate.phone });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Qualified");
  await row.getByRole("link", { name }).click();

  // Application detail.
  await expect(page).toHaveURL(new RegExp(`/applications/${app.id}$`));
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cost (Operators only)" })).toBeVisible();
  const transcript = page.getByRole("region", { name: "Transcript" });
  await expect(transcript).toContainText("Hola, quiero trabajar de repartidor");

  // Claim.
  const actions = page.getByRole("region", { name: "Actions" });
  await actions.getByRole("button", { name: "Claim", exact: true }).click();
  await expect(actions).toContainText("Operator (synthetic) since");
  await expect(actions.getByRole("button", { name: "Release Claim" })).toBeVisible();

  // Takeover: the Candidate is told a team member joined; a Team message reaches them.
  const takeover = page.getByRole("region", { name: "Takeover" });
  await takeover.getByRole("button", { name: "Start Takeover" }).click();
  await expect(takeover).toContainText("You hold this chat.");
  const text = `Hola ${name.split(" ")[0]}, soy del equipo de Grupo Sazón.`;
  await takeover.getByLabel("Message to the Candidate").fill(text);
  await takeover.getByRole("button", { name: "Send" }).click();
  await expect(takeover.getByLabel("Message to the Candidate")).toHaveValue("");
  await expect(transcript).toContainText(text);
  await expect.poll(async () => (await candidate.inbox()).includes(text)).toBe(true);

  // The Candidate's reply reaches the panel live (server-sent events), not the agent.
  await candidate.say("Gracias, quedo atento.");
  await expect(transcript).toContainText("Gracias, quedo atento.");

  // Hand back.
  await takeover.getByLabel(/Summary for the Screening Agent/).fill("Saludé al Candidato; espera la Approval.");
  await takeover.getByRole("button", { name: "Hand back to the Screening Agent" }).click();
  await expect(takeover).toContainText("The Screening Agent holds this chat.");
  await expect(page.getByRole("region", { name: "Timeline" })).toContainText("Takeover handed back");

  // Approve from the review queue.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Review queue" }).click();
  await expect(page.getByRole("tab", { name: /Approvals/ })).toHaveAttribute("aria-selected", "true");
  const card = page.getByRole("article").filter({ has: page.locator(`a[href="/applications/${app.id}"]`) });
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card).toHaveCount(0);

  await expect
    .poll(async () => (await (await api.get(`/api/applications/${app.id}`)).json()).stage)
    .toMatch(/^(approved|interview_booked)$/);
  await page.goto(`/applications/${app.id}`);
  await expect(page.getByRole("region", { name: "Timeline" })).toContainText("Approved");

  await api.dispose();
  await candidate.close();
});

test("the dashboards render the real aggregates for an Operator", async ({ page }) => {
  await logIn(page, OPERATOR);
  const nav = page.getByRole("navigation", { name: "Main" });

  await nav.getByRole("link", { name: "Results" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Results" })).toBeVisible();
  await expect(page.locator(".page-head .muted")).toHaveText(/\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}/);
  await expect(page.getByRole("alert")).toHaveCount(0);

  await nav.getByRole("link", { name: "Economics" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Economics" })).toBeVisible();
  await expect(page.getByText("Cost per conversation")).toBeVisible();
  // No comparison dataset on a fresh stack is a 404 the page explains, not an error.
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// The Operator's Overview (phase 4, operator): landing, the Recruiter's pipeline-only view, tabs by keyboard,
// the 15 s refresh, widths and axe. A Candidate is created first so the pipeline has data; assertions check
// structure and `>= 1`, never exact figures (the ledger on a fresh stack can be empty or null).
const WIDTHS = [768, 1440, 1920] as const;
const COST_PATHS = ["/api/metrics/economics", "/api/metrics/comparison"];

/** Opens an Overview or Economics URL and waits until its data has replaced every skeleton. */
async function open(page: Page, path: string, width = 1440): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  if (path.startsWith("/overview")) await expect(page.getByRole("region", { name: "Pipeline" })).toBeVisible();
  await expect(page.locator(".ui-skeleton")).toHaveCount(0);
  if (path === "/overview" || path === "/economics") await expect(page.getByRole("heading", { name: "Model mix" })).toBeVisible();
  if (path === "/overview?tab=comparison") await expect(page.getByRole("heading", { name: "Model comparison" })).toBeVisible();
}

/** Bars grow in on first paint (about half a second): wait until at least one has width (unless `required` is false,
 * for a page whose charts can be all zero and say so instead of drawing), and the widths stop changing. */
async function drawnBars(page: Page, scope = page.locator("body"), required = true): Promise<number> {
  const widths = () => scope.locator(".recharts-bar-rectangle rect").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().width.toFixed(1)).join(","));
  if (required) await expect.poll(async () => (await widths()).split(",").some((w) => Number(w) > 0)).toBe(true);
  else {
    // Every chart card has either drawn a bar or said it has nothing to draw; a page with no chart passes at once.
    const cards = scope.locator(".ui-card").filter({ has: page.locator(".chart-card__details") });
    await expect
      .poll(() =>
        cards.evaluateAll((els) =>
          els.every(
            (el) =>
              el.querySelector(".chart-card__empty") !== null ||
              [...el.querySelectorAll(".recharts-bar-rectangle rect")].some((rect) => rect.getBoundingClientRect().width > 0),
          ),
        ),
      )
      .toBe(true);
  }
  let last = await widths();
  await expect
    .poll(async () => {
      await page.waitForTimeout(300);
      const now = await widths();
      const settled = now === last;
      last = now;
      return settled;
    })
    .toBe(true);
  return last.split(",").filter((w) => Number(w) > 0).length;
}

const fits = (page: Page) => page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));

test.describe("Overview", () => {
  let candidate: Candidate;

  test.beforeAll(async () => {
    candidate = await Candidate.start("MX");
    await candidate.say("Hola, quiero trabajar de repartidor");
    const api = await apiAs(OPERATOR);
    await applicationFor(api, candidate.phone);
    await api.dispose();
  });

  test.afterAll(async () => {
    await candidate?.close();
  });

  test("the Operator lands on Overview and sees the pipeline, the Outcomes and the economics row", async ({ page }) => {
    await logIn(page, OPERATOR); // asserts the landing is /overview, then moves on to /applications
    await open(page, "/overview");
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    const pipeline = page.getByRole("region", { name: "Pipeline" });
    await expect(pipeline).toBeVisible();
    await expect(page.getByRole("region", { name: "Applications by Outcome" })).toBeVisible();
    const signals = page.getByRole("region", { name: "Signals" });
    await expect(signals.getByRole("link", { name: /Escalations/ })).toHaveAttribute("href", "/queue");
    await expect(signals.getByRole("link", { name: /Qualified, waiting/ })).toHaveAttribute("href", "/applications");
    await expect(page.getByRole("region", { name: "Live economics" })).toBeVisible();
    await expect(page.getByText("Cost per conversation")).toBeVisible();
    await expect(page.getByText("Conversations", { exact: true })).toBeVisible();
    await expect(page.getByText("Latency p50")).toBeVisible();

    // The numbers are in the table view: five groups in order, at least one open Application.
    await pipeline.getByText("Table view").click();
    const rows = pipeline.getByRole("table", { name: "Pipeline (table)" }).getByRole("row");
    await expect(rows).toHaveCount(6);
    const counts = (await rows.locator("td:nth-child(2)").allTextContents()).map((t) => Number(t.replace(/,/g, "")));
    expect(counts).toHaveLength(5);
    expect(counts.reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(1);
    await expect(page.getByRole("tab", { name: "Economics" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("the Pipeline chart draws at least one bar, and so does Results", async ({ page }) => {
    await logIn(page, OPERATOR);
    await open(page, "/overview");
    expect(await drawnBars(page, page.getByRole("region", { name: "Pipeline" }))).toBeGreaterThanOrEqual(1);
    await open(page, "/results");
    await expect(page.getByText("Time to qualify (median)")).toBeVisible();
    expect(await drawnBars(page)).toBeGreaterThanOrEqual(1);
  });

  test("the Puebla Recruiter who opens /overview sees the pipeline, no cost and no economics request", async ({ page }) => {
    const paths: string[] = [];
    page.on("request", (request) => paths.push(new URL(request.url()).pathname));
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto("/overview");
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Pipeline" })).toBeVisible();
    await expect(page.locator(".ui-skeleton")).toHaveCount(0);
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await expect(page.getByText("Cost per conversation")).toHaveCount(0);
    expect(await page.locator("body").innerText()).not.toContain("$");
    expect(paths.filter((path) => COST_PATHS.includes(path))).toEqual([]);
    expect(paths).toContain("/api/metrics/results");
  });

  test("the tabs move from Economics to Comparison by keyboard, and ?tab= follows", async ({ page }) => {
    await logIn(page, OPERATOR);
    await open(page, "/overview");
    const economicsTab = page.getByRole("tab", { name: "Economics" });
    const comparisonTab = page.getByRole("tab", { name: "Comparison" });
    await economicsTab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(comparisonTab).toBeFocused();
    await expect(comparisonTab).toHaveAttribute("aria-selected", "true");
    await expect(page).toHaveURL(/\/overview\?tab=comparison$/);
    await expect(page.getByRole("tabpanel").getByRole("heading", { name: "Model comparison" })).toBeVisible();
    await expect(page.getByLabel("Comparison dataset")).toBeVisible();
    await expect(page.getByLabel("Environment")).toHaveCount(0);
    // No comparison dataset on a fresh stack is a 404 the panel explains, not an error.
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.keyboard.press("Home");
    await expect(economicsTab).toBeFocused();
    await expect(economicsTab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel").getByRole("heading", { name: "Model mix" })).toBeVisible();
  });

  test("a 15 s refresh sends a new /api/metrics/results request", async ({ page }) => {
    await logIn(page, OPERATOR);
    await page.clock.install();
    await open(page, "/overview");
    const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/metrics/results" && response.status() === 200);
    await page.clock.fastForward(15_000);
    await refreshed;
    // The page keeps its content in place: no skeleton came back.
    await expect(page.getByRole("region", { name: "Pipeline" })).toBeVisible();
    await expect(page.locator(".ui-skeleton")).toHaveCount(0);
  });

  for (const width of WIDTHS) {
    test(`no sideways page scroll at ${width}px on /overview (both tabs) and /economics`, async ({ page }) => {
      await logIn(page, OPERATOR);
      for (const path of ["/overview", "/overview?tab=comparison", "/economics"]) {
        await open(page, path, width);
        const sizes = await fits(page);
        expect(sizes.scroll, `${path} at ${width}`).toBeLessThanOrEqual(sizes.inner);
      }
    });
  }

  for (const width of WIDTHS) {
    test(`screenshots of /overview and /economics at ${width}px`, async ({ page }, testInfo) => {
      const dir = process.env.CRIBA_E2E_SHOTS_DIR ?? testInfo.outputPath();
      mkdirSync(dir, { recursive: true });
      await logIn(page, OPERATOR);
      for (const [name, path] of [
        ["overview", "/overview"],
        ["economics", "/economics"],
      ] as const) {
        await open(page, path, width);
        await drawnBars(page, page.locator("body"), false);
        const file = join(dir, `${name}-${width}.png`);
        await page.screenshot({ path: file, fullPage: true });
        expect(statSync(file).size).toBeGreaterThan(0);
      }
    });
  }

  for (const path of ["/overview", "/overview?tab=comparison", "/economics", "/results"]) {
    test(`${path} has no axe violations in light and dark`, async ({ page }) => {
      await logIn(page, OPERATOR);
      await open(page, path);
      if (path === "/results") await expect(page.getByText("Time to qualify (median)")).toBeVisible();
      await expectNoA11yViolations(page);
    });
  }
});
