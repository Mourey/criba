// The Application page read at a glance (operator console, ui-detail): the strip, three columns that
// each scroll on their own with no page scroll at 1920x1080 (M2), the compact Timeline, and the
// Operator-only panel built from the detail response (stub model and stub Jev on the sim stack).
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { apiAs, applicationFor, Candidate, logIn, OPERATOR, PUEBLA_RECRUITER, requireStack, screeningAnswers } from "./support.ts";

requireStack();

interface Detail {
  id: string;
  events: unknown[];
  messages: { id: number; body: string | null }[];
  message_signals?: { message_id: number; fired: string[] }[];
}

async function detailOf(api: APIRequestContext, id: string): Promise<Detail> {
  const response = await api.get(`/api/applications/${id}`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Detail;
}

/** A Candidate screened to Qualified in Puebla, then Takeovers started and handed back through the API
 * as the Operator until the detail response holds at least 20 events. */
async function seededApplication(): Promise<string> {
  const candidate = await Candidate.start("MX");
  await candidate.say("Hola, quiero trabajar de repartidor");
  for (const answer of screeningAnswers()) await candidate.say(answer);
  const api = await apiAs(OPERATOR);
  const app = await applicationFor(api, candidate.phone, "qualified");
  for (let round = 0; round < 15 && (await detailOf(api, app.id)).events.length < 20; round++) {
    const start = await api.post(`/api/applications/${app.id}/takeover`);
    expect(start.status(), await start.text()).toBe(200);
    const back = await api.post(`/api/applications/${app.id}/takeover/handback`, { data: { summary: "Revisión de la Operación." } });
    expect(back.status(), await back.text()).toBe(200);
  }
  expect((await detailOf(api, app.id)).events.length).toBeGreaterThanOrEqual(20);
  await api.dispose();
  await candidate.close();
  return app.id;
}

/** Each column's own scroll box, the page's, and the strip's top. */
async function layout(page: Page) {
  return page.evaluate(() => {
    const cols = [...document.querySelectorAll<HTMLElement>(".detail-columns > *")].map((el) => ({
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      overflowY: getComputedStyle(el).overflowY,
    }));
    return {
      pageScrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
      scrollY: window.scrollY,
      stripTop: document.querySelector(".detail-strip")!.getBoundingClientRect().top,
      cols,
    };
  });
}

/** M2: no page scroll; every column is its own scroll container; the Transcript log scrolls while the
 * window and the strip stay put. */
async function expectNoPageScroll(page: Page, columns?: number) {
  const before = await layout(page);
  expect(before.pageScrollHeight, "document scrollHeight <= innerHeight").toBeLessThanOrEqual(before.innerHeight);
  if (columns === undefined) expect(before.cols.length).toBeGreaterThanOrEqual(3);
  else expect(before.cols).toHaveLength(columns);
  for (const col of before.cols) {
    expect(col.overflowY).toBe("auto");
    expect(col.clientHeight).toBeGreaterThan(0);
  }
  const log = page.getByRole("log", { name: "Transcript" });
  const box = await log.evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
  expect(box.scrollHeight, "the Transcript log overflows and scrolls").toBeGreaterThan(box.clientHeight);
  await log.evaluate((el) => {
    el.scrollTop = 0;
  });
  const top = await log.evaluate((el) => el.scrollTop);
  await log.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  expect(await log.evaluate((el) => el.scrollTop)).toBeGreaterThan(top);
  const after = await layout(page);
  expect(after.scrollY).toBe(0);
  expect(after.stripTop).toBe(before.stripTop);
}

test.describe("Application page at a glance", () => {
  let id = "";

  test.beforeAll(async () => {
    id = await seededApplication();
  });

  test("M2: no page scroll at 1920x1080 for an Operator", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await logIn(page, OPERATOR);
    await page.goto(`/applications/${id}`);
    await expect(page.getByRole("log", { name: "Transcript" })).toBeVisible();
    await expectNoPageScroll(page);
  });

  for (const view of ["Operator", "Recruiter", "Operator under Preview as Recruiter"] as const) {
    test(`M2: no page scroll at 1920x1080 as ${view}, collapsed and with the Timeline expanded`, async ({ page }) => {
      await page.setViewportSize({ width: 1920, height: 1080 });
      await logIn(page, view === "Recruiter" ? PUEBLA_RECRUITER : OPERATOR);
      await page.goto(`/applications/${id}`);
      await expect(page.getByRole("log", { name: "Transcript" })).toBeVisible();
      if (view === "Operator under Preview as Recruiter") {
        await page.getByRole("button", { name: "Preview as Recruiter" }).click();
        await expect(page.locator(".preview-banner")).toBeVisible();
      }
      const operator = view === "Operator";
      await expect(page.getByRole("region", { name: "Operator panel" })).toHaveCount(operator ? 1 : 0);
      await expect(page.getByRole("button", { name: "Operator panel" })).toHaveCount(operator ? 1 : 0);
      await expectNoPageScroll(page, operator ? 4 : 3);

      const timeline = page.getByRole("region", { name: "Timeline" });
      await expect(timeline.getByRole("listitem")).toHaveCount(10);
      await timeline.getByRole("button", { name: /^Show all \d+ events$/ }).click();
      await expect(timeline.getByRole("button", { name: "Show latest 10" })).toHaveAttribute("aria-expanded", "true");
      expect(await timeline.getByRole("listitem").count()).toBeGreaterThanOrEqual(20);
      await expectNoPageScroll(page, operator ? 4 : 3);
    });
  }

  test("stacks the columns below 1280px, the panel last", async ({ page }) => {
    await page.setViewportSize({ width: 1279, height: 900 });
    await logIn(page, OPERATOR);
    await page.goto(`/applications/${id}`);
    await expect(page.getByRole("region", { name: "Operator panel" })).toBeVisible();
    const boxes = await page.locator(".detail-columns > *").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON() as DOMRect));
    expect(boxes).toHaveLength(4);
    expect(boxes[1].top).toBeGreaterThanOrEqual(boxes[0].bottom);
    expect(boxes[2].top).toBeGreaterThanOrEqual(boxes[1].bottom);
    expect(boxes[3].top).toBeGreaterThanOrEqual(boxes[2].bottom);
  });

  test("puts each Timeline event on one line", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await logIn(page, OPERATOR);
    await page.goto(`/applications/${id}`);
    const items = page.getByRole("region", { name: "Timeline" }).getByRole("listitem");
    await expect(items).toHaveCount(10);
    for (const item of await items.all()) {
      const { height, line } = await item.evaluate((el) => {
        const style = getComputedStyle(el);
        const line = style.lineHeight === "normal" ? parseFloat(style.fontSize) * 1.2 : parseFloat(style.lineHeight);
        return { height: el.getBoundingClientRect().height, line };
      });
      expect(height, "one event line is shorter than twice the line height").toBeLessThan(2 * line);
    }
  });

  test("has no axe violations in either theme, panel open and closed, as Operator and as Recruiter", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await logIn(page, OPERATOR);
    await page.goto(`/applications/${id}`);
    await expect(page.getByRole("region", { name: "Operator panel" })).toBeVisible();
    await expectNoA11yViolations(page);
    const toggle = page.getByRole("button", { name: "Operator panel" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("region", { name: "Operator panel" })).toHaveCount(0);
    await expectNoA11yViolations(page);

    await page.context().clearCookies();
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto(`/applications/${id}`);
    await expect(page.getByRole("log", { name: "Transcript" })).toBeVisible();
    await expectNoA11yViolations(page);
  });
});

test("UI9: the panel shows one row per signal entry from stub Jev's cues, and no message text", async ({ page }) => {
  const confused = "no entiendo";
  const human = "Quiero hablar con una persona";
  const candidate = await Candidate.start("MX");
  await candidate.say("Hola, quiero trabajar de repartidor");
  await candidate.say(confused);
  await candidate.say(human);
  const api = await apiAs(OPERATOR);
  const app = await applicationFor(api, candidate.phone);
  let detail = await detailOf(api, app.id);
  await expect
    .poll(async () => {
      detail = await detailOf(api, app.id);
      return (detail.message_signals ?? []).length;
    })
    .toBeGreaterThanOrEqual(2);
  const entries = detail.message_signals ?? [];
  expect(entries.some((e) => e.fired.includes("confused"))).toBe(true);
  expect(entries.some((e) => e.fired.includes("wants_human"))).toBe(true);

  await page.setViewportSize({ width: 1920, height: 1080 });
  await logIn(page, OPERATOR);
  await page.goto(`/applications/${app.id}`);
  const panel = page.getByRole("region", { name: "Operator panel" });
  const rows = panel.getByRole("list", { name: "Jev readings by message" }).getByRole("listitem");
  await expect(rows).toHaveCount(((await (await api.get(`/api/applications/${app.id}/jev-readings`)).json()) as { readings: unknown[] }).readings.length);
  await expect(panel.locator(".ui-tag", { hasText: /^Confused$/ }).first()).toBeVisible();
  await expect(panel.locator(".ui-tag", { hasText: /^Wants a person$/ }).first()).toBeVisible();
  const text = (await panel.textContent()) ?? "";
  expect(text).not.toContain(confused);
  expect(text).not.toContain(human);

  const log = page.getByRole("log", { name: "Transcript" });
  const row = rows.filter({ has: page.locator(".ui-tag", { hasText: /^Wants a person$/ }) }).first();
  await row.getByRole("button", { name: "Show in Transcript" }).click();
  const selected = log.locator("[data-selected]");
  await expect(selected).toHaveCount(1);
  await expect(selected).toContainText(human);
  await expect(selected).toBeInViewport();

  await api.dispose();
  await candidate.close();
});
