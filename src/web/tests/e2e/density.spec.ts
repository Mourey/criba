// Row density on the three list pages (operator console ui-shell, UI4 and measurement M1): how many
// rows are fully inside a 1920x1080 window at scroll 0 on Applications, the Review queue's Rejections
// tab and Interview slots. This is the ASSERTING form (it was a measuring form that printed the counts
// on main, da6c1aa; those counts are the baseline constants below). It seeds, counts, prints the DENSITY
// lines, and asserts the target, the chrome ceiling, the row heights, the coarse-pointer chips and axe.
//
// Definitions (identical in both forms):
// - A row is a `tbody tr` (every table) or a `li.rp-slot`.
// - A row is counted when its bounding box lies fully inside the viewport (top >= 0 and
//   bottom <= innerHeight) with the window scrolled to the top.
// - chrome is the first row's top edge in px: the heading, filters, tabs, intro and table header above it.
// - H (Interview slots only; 0 on the two tables) is the vertical space the day headings take between
//   the counted rows: the sum, over each pair of consecutive counted rows that sit in different
//   `.rp-day` groups, of max(0, next.top - previous.bottom). With no gap between rows of one list
//   that is the heading, its margins and the space between day groups.
// - fit = floor((1080 - chrome - H) / 36).
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { apiAs, applicationFor, BASE_URL, Candidate, logIn, OPERATOR, requireStack } from "./support.ts";

requireStack();

// The seeding is one serial run and the measurements read it with one signed-in browser page.
test.describe.configure({ mode: "serial" });

const VIEWPORT = { width: 1920, height: 1080 } as const;
const ROW_PX = 36;

// baseline:start
const BASELINE_COMMIT = "da6c1aa8eda240cdc45fa50c2ff2eb493bb2c68f";
const BASELINE_ROWS_APPLICATIONS = 12;
const BASELINE_CHROME_APPLICATIONS = 272.48;
const BASELINE_ROWS_REJECTIONS = 12;
const BASELINE_CHROME_REJECTIONS = 270.19;
const BASELINE_ROWS_SLOTS = 9;
const BASELINE_CHROME_SLOTS = 418.95;
// baseline:end

const BASELINE = {
  applications: { rows: BASELINE_ROWS_APPLICATIONS, chrome: BASELINE_CHROME_APPLICATIONS },
  rejections: { rows: BASELINE_ROWS_REJECTIONS, chrome: BASELINE_CHROME_REJECTIONS },
  slots: { rows: BASELINE_ROWS_SLOTS, chrome: BASELINE_CHROME_SLOTS },
} as const;

const CANDIDATES = 40;
const REJECTED = 35;
const SLOTS = 40;

type PageName = keyof typeof BASELINE;

interface Measurement {
  page: PageName;
  visible: number;
  total: number;
  chrome: number;
  H: number;
}

interface PageUnderTest {
  name: PageName;
  path: string;
  rowSelector: string;
}

const PAGES: readonly PageUnderTest[] = [
  { name: "applications", path: "/applications", rowSelector: "tbody tr" },
  { name: "rejections", path: "/queue?tab=rejection", rowSelector: "tbody tr" },
  { name: "slots", path: "/slots", rowSelector: "li.rp-slot" },
];

let context: BrowserContext;
let page: Page;
let operator: APIRequestContext;
let candidates: Candidate[] = [];
let pueblaName = "";
let seedingSeconds = 0;

/** 40 slots 30 minutes apart at :05 and :35 (minutes the seed and recruiter-pages.spec.ts never use),
 * starting 06:05 UTC a week after the sim clock. The last starts 19.5 hours later, so the run spans one
 * Puebla calendar day (UTC-6) and sits on one day heading unless the Location's zone differs. */
function slotTimes(clockNow: string): { starts_at: string; ends_at: string }[] {
  const day = new Date(Date.parse(clockNow) + 7 * 24 * 3600 * 1000);
  const first = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), 6, 5);
  const iso = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");
  return Array.from({ length: SLOTS }, (_, i) => {
    const start = first + i * 30 * 60000;
    return { starts_at: iso(start), ends_at: iso(start + 30 * 60000) };
  });
}

async function seed(): Promise<void> {
  const began = Date.now();
  // 40 Candidates in parallel, one message each: each one gets an Application.
  candidates = await Promise.all(Array.from({ length: CANDIDATES }, () => Candidate.start("MX")));
  await Promise.all(candidates.map((candidate) => candidate.say("Hola, quiero trabajar de repartidor")));
  operator = await apiAs(OPERATOR);
  const applications = await Promise.all(candidates.map((candidate) => applicationFor(operator, candidate.phone)));

  // 35 of the 40 Applications rejected: Rejections table rows, each with a note on its second line.
  for (const application of applications.slice(0, REJECTED)) {
    const response = await operator.post(`/api/applications/${application.id}/rejection`, {
      data: { reason: "other", note: "Density measurement: rejected so the Rejections table has rows." },
    });
    expect(response.ok(), `rejection of ${application.id}: ${response.status()} ${await response.text()}`).toBeTruthy();
  }

  // 40 slots at a Puebla Location a week after the sim clock.
  const clock = (await (await operator.get("/api/sim/clock")).json()) as { now: string };
  const locations = (await (await operator.get("/api/locations")).json()) as { items: { id: string; name: string }[] };
  const puebla = locations.items.find((l) => l.name.includes("Puebla"));
  if (!puebla) throw new Error("no Puebla Location in GET /api/locations");
  pueblaName = puebla.name;
  const response = await operator.post("/api/slots", { data: { location_id: puebla.id, slots: slotTimes(clock.now) } });
  expect(response.status(), `POST /api/slots (${SLOTS} slots at ${puebla.name}): ${await response.text()}`).toBe(201);

  seedingSeconds = Math.round((Date.now() - began) / 100) / 10;
  console.log(`DENSITY-SEED ${JSON.stringify({ candidates: CANDIDATES, rejected: REJECTED, slots: SLOTS, location: pueblaName, seconds: seedingSeconds })}`);
}

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  test.setTimeout(900_000);
  await seed();
  context = await browser.newContext({ baseURL: BASE_URL, viewport: VIEWPORT });
  page = await context.newPage();
  await logIn(page, OPERATOR);
  console.log(`DENSITY-BASELINE ${JSON.stringify({ commit: BASELINE_COMMIT, ...BASELINE })}`);
});

test.afterAll(async () => {
  await context?.close();
  await operator?.dispose();
  for (const candidate of candidates) await candidate.close();
});

/** Opens a page at 1920x1080, selects the Puebla Location on Interview slots, and waits until the
 * skeletons are gone and the row count has stopped changing; leaves the window at scroll 0. */
async function openPage(target: PageUnderTest): Promise<void> {
  await page.setViewportSize(VIEWPORT);
  await page.goto(target.path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  if (target.name === "slots") {
    const select = page.locator("label.rp-field select.rp-select");
    await expect(select).toBeVisible();
    await select.selectOption({ label: pueblaName });
    await expect(select.locator("option:checked")).toHaveText(pueblaName);
  }
  const rows = page.locator(target.rowSelector);
  await expect(page.locator(".ui-skeleton")).toHaveCount(0);
  await expect(rows.first()).toBeVisible();
  if (target.name === "slots") await expect.poll(() => rows.count()).toBeGreaterThanOrEqual(SLOTS);
  // The row count must hold still across two reads, so a late refetch cannot change it mid-measurement.
  let previous = -1;
  await expect
    .poll(
      async () => {
        const now = await rows.count();
        const settled = now === previous;
        previous = now;
        return settled;
      },
      { intervals: [300] },
    )
    .toBe(true);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.scrollTo(0, 0));
}

/** Counts and measures the rows of an open page (see the definitions at the top of the file). */
async function measure(target: PageUnderTest): Promise<Measurement> {
  const raw = await page.evaluate((selector) => {
    const rows = [...document.querySelectorAll(selector)];
    const boxes = rows.map((row) => {
      const box = row.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, day: row.closest(".rp-day") };
    });
    const counted = boxes.filter((b) => b.top >= 0 && b.bottom <= window.innerHeight);
    let gaps = 0;
    for (let i = 1; i < counted.length; i += 1) {
      if (counted[i].day !== counted[i - 1].day) gaps += Math.max(0, counted[i].top - counted[i - 1].bottom);
    }
    return { total: rows.length, visible: counted.length, chrome: boxes.length > 0 ? boxes[0].top : 0, H: gaps };
  }, target.rowSelector);
  const round = (value: number) => Math.round(value * 100) / 100;
  return {
    page: target.name,
    visible: raw.visible,
    total: raw.total,
    chrome: round(raw.chrome),
    H: target.name === "slots" ? round(raw.H) : 0,
  };
}

/** fit = floor((1080 - chrome - H) / 36), for the asserting form. */
function fit(measurement: Measurement): number {
  return Math.floor((VIEWPORT.height - measurement.chrome - measurement.H) / ROW_PX);
}

/** The geometry of every row of an open page: its height, its top, whether it holds a Button, and
 * which day group (slots) it sits in. */
async function rowGeometry(target: PageUnderTest) {
  return page.evaluate((selector) => {
    const rows = [...document.querySelectorAll(selector)];
    const days = [...new Set(rows.map((row) => row.closest(".rp-day")))];
    return rows.map((row) => {
      const box = row.getBoundingClientRect();
      return { height: box.height, top: box.top, hasButton: row.querySelector(".ui-btn") !== null, day: days.indexOf(row.closest(".rp-day")) };
    });
  }, target.rowSelector);
}

// Asserting form: the target is min(2 x baseline rows, fit), fit from this run's chrome and H.
for (const target of PAGES) {
  test(`${target.name} at 1920x1080: rows per screen, chrome and row heights`, async () => {
    await openPage(target);
    const measurement = await measure(target);
    const baseline = BASELINE[target.name];
    const goal = Math.min(2 * baseline.rows, fit(measurement));
    console.log(`DENSITY ${JSON.stringify(measurement)}`);
    console.log(
      `DENSITY-FIT ${JSON.stringify({ page: target.name, fit: fit(measurement), baselineRows: baseline.rows, baselineChrome: baseline.chrome, target: goal, seedingSeconds, commit: BASELINE_COMMIT })}`,
    );

    // The seeded rows overflow the window, so the count is a real measurement.
    expect(measurement.visible, `${target.name}: every row is visible, so the seeding did not overflow 1080 px`).toBeLessThan(measurement.total);
    expect(measurement.visible, `${target.name}: visible rows against min(2 x ${baseline.rows}, fit ${fit(measurement)})`).toBeGreaterThanOrEqual(goal);
    expect(measurement.chrome, `${target.name}: chrome against main's ${baseline.chrome}`).toBeLessThanOrEqual(baseline.chrome);

    const rows = await rowGeometry(target);
    if (target.name === "slots") {
      for (const row of rows.filter((r) => r.hasButton)) expect(Math.abs(row.height - ROW_PX)).toBeLessThanOrEqual(1);
      for (let i = 1; i < rows.length; i += 1) {
        if (rows[i].day === rows[i - 1].day) expect(Math.abs(rows[i].top - rows[i - 1].top - ROW_PX)).toBeLessThanOrEqual(1);
      }
    } else {
      for (const row of rows) expect(Math.abs(row.height - ROW_PX)).toBeLessThanOrEqual(1);
    }

    await expectNoA11yViolations(page);
  });
}

test("a filter chip is at least 44px high under a coarse pointer", async ({ browser }) => {
  const touch = await browser.newContext({ baseURL: BASE_URL, viewport: VIEWPORT, hasTouch: true, storageState: await context.storageState() });
  try {
    const touchPage = await touch.newPage();
    await touchPage.goto("/applications");
    await expect(touchPage.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await touchPage.evaluate(() => window.matchMedia("(pointer: coarse)").matches), "pointer: coarse is not emulated").toBe(true);
    // The chips sit behind the "More filters" disclosure.
    await touchPage.getByRole("button", { name: /^More filters/ }).click();
    const chips = touchPage.locator(".rp-chip:visible");
    await expect(chips.first()).toBeVisible();
    for (const box of await chips.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height))) expect(box).toBeGreaterThanOrEqual(44);
  } finally {
    await touch.close();
  }
});
