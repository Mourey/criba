// The Recruiter's working pages in a real browser (recruiter-pages, RP11 and RP12): axe in both themes,
// target sizes, the keyboard walk on Applications, no sideways page scroll at 768, 1440 and 1920, the
// sticky Applications header, and screenshots of the four routes. Candidates come through the web chat
// API on the sim stack, so the checks run on real rows.
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { apiAs, applicationFor, BASE_URL, Candidate, logIn, OPERATOR, PUEBLA_RECRUITER, requireStack, screeningAnswers } from "./support.ts";

requireStack();

// One signed-in browser page for the whole file, run in order: the specs below only read, except the
// detail-page test, which starts a Takeover and hands it back.
test.describe.configure({ mode: "serial" });

const WIDTHS = [768, 1440, 1920] as const;
const ROUTES = [
  { name: "applications", path: "/applications", heading: "Applications" },
  { name: "queue", path: "/queue", heading: "Review queue" },
  { name: "slots", path: "/slots", heading: "Interview slots" },
  { name: "results", path: "/results", heading: "Results" },
] as const;
const QUEUE_TABS = ["approval", "review", "escalation", "rejection"] as const;
// The same tags as a11y.ts, for the scoped run on the Application page.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const shots = join(process.env.TMPDIR ?? tmpdir(), "criba-recruiter-pages", "shots");

let context: BrowserContext;
let page: Page;
let operator: APIRequestContext;
let candidates: Candidate[] = [];
let applicationId = "";

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  test.setTimeout(300_000);
  // Five Candidates in Puebla, each screened to Qualified, so the list, the queue and a transcript are real.
  candidates = await Promise.all(Array.from({ length: 5 }, () => Candidate.start("MX")));
  await Promise.all(
    candidates.map(async (candidate) => {
      await candidate.say("Hola, quiero trabajar de repartidor");
      for (const answer of screeningAnswers()) await candidate.say(answer);
    }),
  );
  operator = await apiAs(OPERATOR);
  applicationId = (await applicationFor(operator, candidates[0].phone, "qualified")).id;
  for (const candidate of candidates.slice(1)) await applicationFor(operator, candidate.phone, "qualified");

  // A few slots at a Puebla Location, a week after the sim clock.
  const clock = (await (await operator.get("/api/sim/clock")).json()) as { now: string };
  const locations = (await (await operator.get("/api/locations")).json()) as { items: { id: string; name: string }[] };
  const puebla = locations.items.find((l) => l.name.includes("Puebla"));
  if (puebla) {
    const day = new Date(Date.parse(clock.now) + 7 * 24 * 3600 * 1000);
    const slots = [16, 17, 18].map((hour) => {
      const start = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hour));
      return { starts_at: start.toISOString().replace(".000Z", "Z"), ends_at: new Date(start.getTime() + 30 * 60000).toISOString().replace(".000Z", "Z") };
    });
    await operator.post("/api/slots", { data: { location_id: puebla.id, slots } });
  }

  context = await browser.newContext({ baseURL: BASE_URL, viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  await logIn(page, PUEBLA_RECRUITER);
});

test.afterAll(async () => {
  await context?.close();
  await operator?.dispose();
  for (const candidate of candidates) await candidate.close();
});

/** Opens a route at a width and waits until its data has replaced every skeleton. */
async function openRoute(path: string, width = 1440, height = 900): Promise<void> {
  await page.setViewportSize({ width, height });
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".ui-skeleton")).toHaveCount(0);
}

test.describe("axe, both themes", () => {
  test("Applications", async () => {
    await openRoute("/applications");
    await expect(page.getByRole("row").nth(1)).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("Applications, More filters opened by hand", async () => {
    await openRoute("/applications");
    await expect(page.getByRole("row").nth(1)).toBeVisible();
    await page.getByRole("button", { name: /More filters/ }).click();
    await expect(page.getByLabel("Country")).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("Applications, with filters set and chips checked", async () => {
    await openRoute("/applications?stage=qualified&outcome=knockout&flag=takeover&country=MX");
    // Four hidden filters are set, so the disclosure opened by itself.
    await expect(page.getByRole("button", { name: /More filters/ })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("region", { name: "Applications table" })).not.toHaveAttribute("aria-busy", "true");
    await expectNoA11yViolations(page);
  });

  for (const tab of QUEUE_TABS) {
    test(`Review queue, ${tab} tab`, async () => {
      await openRoute(`/queue?tab=${tab}`);
      await expect(page.getByRole("tab", { selected: true })).toContainText(new RegExp(tab === "approval" ? "Approvals" : tab === "review" ? "Reviews" : tab === "escalation" ? "Escalations" : "Rejections"));
      await expectNoA11yViolations(page);
    });
  }

  test("Review queue, resolved items", async () => {
    await openRoute("/queue?status=resolved");
    await expectNoA11yViolations(page);
  });

  test("Slots", async () => {
    await openRoute("/slots");
    await expect(page.getByRole("button", { name: "Add to batch" })).toBeVisible();
    await expectNoA11yViolations(page);
  });
});

test.describe("Application page: Transcript and Takeover panel", () => {
  /** axe on the two wrappers only, in light and dark, with reduced motion. The rest of the page is
   * ApplicationDetail.tsx's own markup, which another child restyles. */
  async function expectScopedAxeClean(): Promise<void> {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const original = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    const found: string[] = [];
    try {
      for (const theme of ["light", "dark"] as const) {
        await page.evaluate((value) => document.documentElement.setAttribute("data-theme", value), theme);
        const { violations } = await new AxeBuilder({ page }).withTags(TAGS).include(".rp-transcript").include(".rp-takeover").analyze();
        for (const v of violations) found.push(`[${theme}] ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
      }
    } finally {
      await page.evaluate((value) => {
        if (value === null) document.documentElement.removeAttribute("data-theme");
        else document.documentElement.setAttribute("data-theme", value);
      }, original);
    }
    expect(found, found.join("\n")).toEqual([]);
  }

  test("the log and the panel pass axe while the Screening Agent holds the chat", async () => {
    await openRoute(`/applications/${applicationId}`);
    const log = page.getByRole("log", { name: "Transcript" });
    await expect(log).toBeVisible();
    await expect(log).toHaveClass(/rp-transcript/);
    await expect(log).toHaveAttribute("tabindex", "0");
    await expect(page.getByRole("region", { name: "Takeover" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Start Takeover" })).toBeVisible();
    await expectScopedAxeClean();
  });

  test("the log and the composer pass axe while the Recruiter holds the chat", async () => {
    await openRoute(`/applications/${applicationId}`);
    const takeover = page.getByRole("region", { name: "Takeover" });
    await takeover.getByRole("button", { name: "Start Takeover" }).click();
    await expect(takeover.getByLabel("Message to the Candidate")).toBeVisible();
    await expectScopedAxeClean();
    await takeover.getByRole("button", { name: "Hand back to the Screening Agent" }).click();
    await expect(takeover.getByRole("button", { name: "Start Takeover" })).toBeVisible();
  });

  test("the scrolling log takes the keyboard focus and shows a ring", async () => {
    await openRoute(`/applications/${applicationId}`);
    const log = page.getByRole("log", { name: "Transcript" });
    await log.focus();
    await expect(log).toBeFocused();
    const style = await log.evaluate((el) => {
      const s = getComputedStyle(el);
      return { outlineStyle: s.outlineStyle, outlineWidth: s.outlineWidth, boxShadow: s.boxShadow };
    });
    expect(style.outlineStyle !== "none" && style.outlineWidth !== "0px").toBe(true);
  });
});

test.describe("target sizes and the keyboard", () => {
  async function expectTargets(locator: Locator): Promise<void> {
    const count = await locator.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const box = await locator.nth(i).boundingBox();
      const label = await locator.nth(i).evaluate((el) => el.textContent?.trim() ?? el.tagName);
      expect(box, label).not.toBeNull();
      expect(box!.width, `${label} width`).toBeGreaterThanOrEqual(24);
      expect(box!.height, `${label} height`).toBeGreaterThanOrEqual(24);
    }
  }

  test("chips and buttons on Applications are at least 24 by 24", async () => {
    await openRoute("/applications?stage=qualified");
    await expectTargets(page.locator(".rp-chip"));
    await expectTargets(page.locator(".page button:visible"));
  });

  test("tabs and actions in the Review queue are at least 24 by 24", async () => {
    await openRoute("/queue");
    await expect(page.getByRole("button", { name: "Approve" }).first()).toBeVisible();
    await expectTargets(page.locator(".page [role=tab]"));
    await expectTargets(page.locator(".page button:visible"));
  });

  test("buttons on Slots are at least 24 by 24", async () => {
    await openRoute("/slots");
    await expect(page.getByRole("button", { name: "Move" }).first()).toBeVisible();
    await expectTargets(page.locator(".page button:visible"));
  });

  test("More filters is closed by default, and a hidden filter in the URL opens it with its count", async () => {
    await openRoute("/applications");
    const more = page.getByRole("button", { name: /More filters/ });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await expect(more).toHaveAccessibleName("More filters");
    await expect(page.getByLabel("Zone", { exact: true })).toBeHidden();
    await openRoute("/applications?stage=zone&country=MX&status=open");
    await expect(more).toHaveAttribute("aria-expanded", "true");
    await expect(more).toHaveAccessibleName("More filters 2 active");
    await expect(page.getByLabel("Zone", { exact: true })).toBeChecked();
    await expect(page.getByLabel("Country")).toHaveValue("MX");
  });

  test("the keyboard reaches every filter control and a chip, and every stop shows a ring", async () => {
    await openRoute("/applications");
    const stop = () =>
      page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el) return { name: "none", ring: false, chip: false };
        const visible = (node: Element) => {
          const s = getComputedStyle(node);
          return (s.outlineStyle !== "none" && s.outlineWidth !== "0px") || s.boxShadow !== "none";
        };
        const chipLabel = el.closest("label.rp-chip");
        // The calendar button inside a date input is a native stop: the browser draws its own ring there and
        // the input itself no longer matches :focus-visible.
        const nativePicker = el instanceof HTMLInputElement && el.type === "date" && !el.matches(":focus-visible");
        // A chip's checkbox is hidden: its label carries the ring.
        const ring = nativePicker || visible(el) || (chipLabel ? visible(chipLabel) : false);
        const label = (el as HTMLInputElement).labels?.[0]?.textContent?.trim() ?? "";
        const text = el instanceof HTMLButtonElement ? (el.textContent?.trim() ?? "") : "";
        return { name: label || el.getAttribute("aria-label") || text || el.tagName, ring, chip: Boolean(chipLabel) };
      });

    // Closed to begin with; the walk opens it from the keyboard once Tab reaches the button.
    await expect(page.getByRole("button", { name: /More filters/ })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByLabel("Country")).toBeHidden();
    await page.getByLabel("Search Candidate").focus();
    const visited: string[] = [];
    let reachedChip = false;
    let opened = false;
    for (let i = 0; i < 40 && !reachedChip; i++) {
      const state = await stop();
      expect(state.ring, `focus ring on "${state.name}"`).toBe(true);
      visited.push(state.name);
      reachedChip = state.chip;
      if (state.name.startsWith("More filters") && !opened) {
        // The disclosure opens from the keyboard; the hidden controls join the Tab order after it.
        await page.keyboard.press("Enter");
        await expect(page.getByRole("button", { name: /More filters/ })).toHaveAttribute("aria-expanded", "true");
        opened = true;
      }
      if (!reachedChip) await page.keyboard.press("Tab");
    }
    expect(opened, "Tab reaches More filters").toBe(true);
    expect(reachedChip).toBe(true);
    for (const label of ["Search Candidate", "Location", "Status", "Claim", "Sort", "More filters", "Country", "Created from", "Created to"]) {
      expect(visited.some((name) => name.startsWith(label)), `Tab reaches ${label}`).toBe(true);
    }
    await page.keyboard.press("Space");
    await expect(page).toHaveURL(/stage=/);
  });
});

test.describe("viewports", () => {
  for (const route of ROUTES) {
    for (const width of WIDTHS) {
      test(`${route.name} at ${width}: no sideways page scroll, screenshot`, async () => {
        await openRoute(route.path, width);
        if (route.name === "applications") await expect(page.getByRole("row").nth(1)).toBeVisible();
        if (route.name === "queue") await expect(page.getByRole("button", { name: "Approve" }).first()).toBeVisible();
        if (route.name === "slots") await expect(page.getByRole("button", { name: "Move" }).first()).toBeVisible();
        if (route.name === "results") await expect(page.getByText("Time to qualify (median)")).toBeVisible();
        const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
        expect(sizes.scroll, `${route.path} at ${width}`).toBeLessThanOrEqual(sizes.inner);
        mkdirSync(shots, { recursive: true });
        await page.screenshot({ path: join(shots, `${route.name}-${width}.png`), fullPage: true });
      });
    }
  }

  test("the Applications table scrolls inside its region at 768 and not at 1440", async () => {
    await openRoute("/applications", 768);
    const region = page.getByRole("region", { name: "Applications table" });
    await expect(region).toHaveAttribute("tabindex", "0");
    const narrow = await region.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
    expect(narrow.scroll).toBeGreaterThan(narrow.client);

    await openRoute("/applications", 1440);
    const wide = await page.getByRole("region", { name: "Applications table" }).evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
    expect(wide.scroll).toBeLessThanOrEqual(wide.client);
  });

  test("the Applications header is sticky at 1440 and stays under the top of the window while the page scrolls", async () => {
    // A short window, so the rows overflow it and the page really scrolls.
    await openRoute("/applications", 1440, 480);
    const header = page.getByRole("columnheader", { name: "Candidate" });
    await expect(header).toBeVisible();
    expect(await header.evaluate((el) => getComputedStyle(el).position)).toBe("sticky");
    const scrollable = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
    expect(scrollable).toBeGreaterThan(100);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const top = await header.evaluate((el) => el.getBoundingClientRect().top);
    expect(top).toBeGreaterThanOrEqual(0);
    expect(top).toBeLessThanOrEqual(2);
  });
});
