// A Recruiter sees only their own Locations, no economics navigation and no cost figures, and
// the API sends them no cost fields at all (the UI is not the only guard).
import { expect, test, type Response } from "@playwright/test";
import { apiAs, applicationFor, Candidate, candidateName, costKeysIn, logIn, OPERATOR, PUEBLA_RECRUITER, requireStack } from "./support.ts";

requireStack();

test("a Recruiter sees only their Locations and no cost data", async ({ page }) => {
  // One Candidate in Puebla (the Recruiter's city) and one in Madrid (not theirs).
  const pueblaName = candidateName();
  const puebla = await Candidate.start("MX");
  await puebla.say("Hola");
  await puebla.say(pueblaName);
  await puebla.say("Centro, Puebla");
  const madrid = await Candidate.start("ES");
  await madrid.say("Hola");
  await madrid.say(candidateName());
  await madrid.say("Madrid, Chamberí");
  const operator = await apiAs(OPERATOR);
  const pueblaApp = await applicationFor(operator, puebla.phone, "driver_license");
  const madridApp = await applicationFor(operator, madrid.phone, "driver_license");

  // Every JSON body the app receives is checked for cost fields.
  const leaks: string[] = [];
  const checks: Promise<void>[] = [];
  page.on("response", (response: Response) => {
    const type = response.headers()["content-type"] ?? "";
    if (!response.url().includes("/api/") || !type.includes("application/json")) return;
    checks.push(
      response
        .json()
        .then((body) => {
          for (const key of costKeysIn(body)) leaks.push(`${response.url()}: ${key}`);
        })
        .catch(() => undefined),
    );
  });

  await logIn(page, PUEBLA_RECRUITER);
  await expect(page.getByTestId("role-badge")).toHaveText("Recruiter");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Applications" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Economics" })).toHaveCount(0);

  // The Location filter offers only the Recruiter's Locations.
  const locationOptions = page.getByLabel("Location").locator("option");
  await expect(locationOptions).toHaveText(["All Locations", "Sazón Puebla Angelópolis", "Sazón Puebla Centro", "Sazón Puebla La Paz"]);

  // The list holds the Puebla Application, never the Madrid one.
  const search = page.getByRole("searchbox", { name: "Search Candidate" });
  await search.fill(madrid.phone);
  await expect(page.getByText("No Applications match these filters.")).toBeVisible();
  await search.fill(puebla.phone);
  const row = page.getByRole("row").filter({ hasText: puebla.phone });
  await expect(row).toHaveCount(1);
  for (const location of await page.getByRole("row").locator("td:nth-child(2)").allTextContents()) {
    expect(location).toMatch(/Puebla/);
  }

  // Detail: no cost card.
  await row.getByRole("link", { name: pueblaName }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${pueblaApp.id}$`));
  await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Cost/ })).toHaveCount(0);
  await expect(page.getByText(/\$\d/)).toHaveCount(0);

  // Out of scope: the Madrid Application does not exist for this Recruiter.
  await page.goto(`/applications/${madridApp.id}`);
  await expect(page.getByRole("heading", { name: "Application not found" })).toBeVisible();

  // Economics is refused in the UI and by the API.
  await page.goto("/economics");
  await expect(page.getByRole("alert")).toHaveText("The economics views are for Operators only.");
  const economics = await page.request.get("/api/metrics/economics");
  expect(economics.status()).toBe(403);

  // Results, the queue and the slots render without cost figures either.
  await page.goto("/results");
  await expect(page.getByRole("heading", { level: 1, name: "Results" })).toBeVisible();
  await page.goto("/queue");
  await expect(page.getByRole("heading", { level: 1, name: "Review queue" })).toBeVisible();
  await page.goto("/slots");
  await expect(page.getByRole("heading", { level: 1, name: "Interview slots" })).toBeVisible();

  await Promise.all(checks);
  expect(leaks).toEqual([]);

  await operator.dispose();
  await puebla.close();
  await madrid.close();
});

// Today and the Application page (recruiter-today): path length, Interviews, accessibility, keyboard,
// viewports and reduced motion. Everything runs on the sim stack with the stub model.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { request as newRequest, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { BASE_URL, screeningAnswers } from "./support.ts";

interface Counted {
  __clicks: number;
  __nav: string[];
}

/** Counts trusted clicks (capture phase, so nothing can stop them first) and logs History API navigation. */
async function installCounters(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as Counted;
    w.__clicks = 0;
    w.__nav = [];
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) w.__clicks += 1;
      },
      true,
    );
    for (const method of ["pushState", "replaceState"] as const) {
      const original = history[method].bind(history);
      history[method] = (...args: Parameters<History["pushState"]>) => {
        original(...args);
        w.__nav.push(location.pathname + location.search);
      };
    }
    window.addEventListener("popstate", () => w.__nav.push(location.pathname + location.search));
  });
}

const resetClicks = (page: Page) => page.evaluate(() => void ((window as unknown as Counted).__clicks = 0));
const clicks = (page: Page) => page.evaluate(() => (window as unknown as Counted).__clicks);
const navLog = (page: Page) => page.evaluate(() => (window as unknown as Counted).__nav);

interface Screened {
  chat: Candidate;
  id: string;
}

/** A Candidate in Puebla, screened to Qualified through the web chat (stub model). */
async function qualified(recruiter: APIRequestContext): Promise<Screened> {
  const chat = await Candidate.start("MX");
  await chat.say("Hola, quiero trabajar de repartidor");
  for (const answer of screeningAnswers()) await chat.say(answer);
  const app = await applicationFor(recruiter, chat.phone, "qualified");
  return { chat, id: app.id };
}

const todayRow = (page: Page, id: string): Locator =>
  page.getByRole("region", { name: "Approvals waiting" }).getByRole("row").filter({ has: page.locator(`a[href="/applications/${id}"]`) });

const stageOf = async (api: APIRequestContext, id: string): Promise<string> => (await (await api.get(`/api/applications/${id}`)).json()).stage;

interface Focus {
  tag: string;
  name: string;
  href: string | null;
  /** The first link in the focused element's table row: which row an action belongs to. */
  rowHref: string | null;
  ring: boolean;
}

const focused = (page: Page): Promise<Focus> =>
  page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const style = getComputedStyle(el);
    return {
      tag: el.tagName.toLowerCase(),
      name: (el.getAttribute("aria-label") ?? el.textContent ?? "").replace(/\s+/g, " ").trim(),
      href: el.getAttribute("href"),
      rowHref: el.closest("tr")?.querySelector("a")?.getAttribute("href") ?? null,
      ring: style.outlineStyle !== "none" && style.outlineWidth !== "0px",
    };
  });

/** Tab from the start of the page until every wanted element has had focus, in at most `max` presses. */
async function tabThrough(page: Page, wanted: Record<string, (f: Focus) => boolean>, max = 60, fromStart = true): Promise<Record<string, number>> {
  if (fromStart) await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const reached: Record<string, number> = {};
  for (let press = 1; press <= max; press++) {
    await page.keyboard.press("Tab");
    const now = await focused(page);
    for (const [label, matches] of Object.entries(wanted)) {
      if (reached[label] === undefined && matches(now)) {
        reached[label] = press;
        expect(now.ring, `${label} shows a focus ring`).toBe(true);
      }
    }
    if (Object.keys(reached).length === Object.keys(wanted).length) return reached;
  }
  throw new Error(`not every element was reached in ${max} Tab presses; reached: ${JSON.stringify(reached)}`);
}

const shotsDir = process.env.CRIBA_E2E_SHOTS_DIR ?? join(process.cwd(), "test-results", "shots");

test.describe("Today and the Application page: one-click path", () => {
  test("Today to Approve: each step is one click, and no list or queue is visited", async ({ page }) => {
    const recruiter = await apiAs(PUEBLA_RECRUITER);
    const [a, b] = await Promise.all([qualified(recruiter), qualified(recruiter)]);

    await installCounters(page);
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto("/today");
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
    // From here on, count navigation: logIn itself passes through /applications.
    const visited: string[] = [];
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname);
    });

    // Open A from its Approvals row: one click.
    const rowA = todayRow(page, a.id);
    await expect(rowA).toHaveCount(1);
    await resetClicks(page);
    await rowA.locator(`a[href="/applications/${a.id}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/applications/${a.id}$`));
    expect(await clicks(page), "open").toBe(1);

    // Claim: one click.
    const actions = page.getByRole("region", { name: "Actions" });
    await expect(actions.getByRole("button", { name: "Approve" })).toBeVisible();
    await resetClicks(page);
    await actions.getByRole("button", { name: "Claim", exact: true }).click();
    await expect(actions).toContainText(/Recruiter .* since/);
    expect(await clicks(page), "Claim").toBe(1);

    // Start Takeover: one click.
    const takeover = page.getByRole("region", { name: "Takeover" });
    await resetClicks(page);
    await takeover.getByRole("button", { name: "Start Takeover" }).click();
    await expect(takeover).toContainText("You hold this chat.");
    expect(await clicks(page), "Start Takeover").toBe(1);

    // Typing and Send: reported separately, and Send is one click.
    const text = `Hola ${candidateName().split(" ")[0]}, soy del equipo de Grupo Sazón.`;
    await takeover.getByLabel("Message to the Candidate").fill(text);
    await resetClicks(page);
    await takeover.getByRole("button", { name: "Send" }).click();
    await expect(takeover.getByLabel("Message to the Candidate")).toHaveValue("");
    const sendClicks = await clicks(page);
    expect(sendClicks, "Send").toBe(1);
    await expect.poll(async () => (await a.chat.inbox()).includes(text)).toBe(true);

    // Hand back: one click (the summary is typed first).
    await takeover.getByLabel(/Summary for the Screening Agent/).fill("Saludé al Candidato; espera la Approval.");
    await resetClicks(page);
    await takeover.getByRole("button", { name: "Hand back to the Screening Agent" }).click();
    await expect(takeover).toContainText("The Screening Agent holds this chat.");
    expect(await clicks(page), "Hand back").toBe(1);

    // Approve on the Application: one click.
    await resetClicks(page);
    await actions.getByRole("button", { name: "Approve" }).click();
    await expect(actions.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await expect(actions).toBeFocused();
    expect(await clicks(page), "Approve on the Application").toBe(1);
    await expect.poll(() => stageOf(recruiter, a.id)).not.toBe("qualified");

    // B: approved with one click on its row on Today (reached by the Main navigation's Today link).
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Today" }).click();
    await expect(page).toHaveURL(/\/today$/);
    const rowB = todayRow(page, b.id);
    await expect(rowB).toHaveCount(1);
    await resetClicks(page);
    await rowB.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("region", { name: "Approvals waiting" }).getByRole("status")).toHaveText(/^Approved /);
    expect(await clicks(page), "Approve on a Today row").toBe(1);
    await expect.poll(() => stageOf(recruiter, b.id)).not.toBe("qualified");
    await expect(todayRow(page, b.id)).toHaveCount(0);

    // Neither path went through the Applications list or the Review queue.
    const pathsSeen = [...visited, ...(await navLog(page)).map((p) => p.split("?")[0])];
    expect(pathsSeen.filter((p) => p === "/applications" || p === "/queue")).toEqual([]);
    expect(pathsSeen).toContain(`/applications/${a.id}`);

    await recruiter.dispose();
    await a.chat.close();
    await b.chat.close();
  });
});

/** The calendar day of an instant in a time zone, as YYYY-MM-DD (the page's "today" rule, by the Location's own zone). */
const dayIn = (instant: Date, timeZone: string) =>
  new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(instant);

test("Interviews today lists a booked Slot by its Location's day on the sim clock", async ({ page }) => {
  const recruiter = await apiAs(PUEBLA_RECRUITER);
  const { chat, id } = await qualified(recruiter);

  // Approve through the API, as the Recruiter, then book a Slot a few minutes after the sim clock's now.
  const queue = (await (await recruiter.get("/api/queue", { params: { kind: "approval", status: "open", limit: "200" } })).json()) as {
    items: { id: string; application: { id: string } }[];
  };
  const item = queue.items.find((i) => i.application.id === id);
  expect(item, "an open approval for the Candidate").toBeTruthy();
  const approved = await recruiter.post(`/api/queue/approvals/${item!.id}`, { data: { decision: "approved" } });
  expect(approved.status(), await approved.text()).toBe(200);
  await expect.poll(() => stageOf(recruiter, id)).toBe("approved");

  const detail = (await (await recruiter.get(`/api/applications/${id}`)).json()) as { locations: { id: string }[] };
  const locationId = detail.locations[0].id;
  const timeZone = ((await (await recruiter.get("/api/locations")).json()) as { items: { id: string; timezone: string }[] }).items.find(
    (l) => l.id === locationId,
  )!.timezone;
  const clock = (await (await recruiter.get("/api/sim/clock")).json()) as { now: string };
  const now = new Date(clock.now);
  const start = new Date(Math.ceil((now.getTime() + (5 + Math.floor(Math.random() * 25)) * 60_000) / 60_000) * 60_000);
  const end = new Date(start.getTime() + 30 * 60_000);
  const created = await recruiter.post("/api/slots", { data: { location_id: locationId, slots: [{ starts_at: start.toISOString(), ends_at: end.toISOString() }] } });
  expect(created.status(), await created.text()).toBe(201);
  const slotId = ((await created.json()) as { items: { id: string }[] }).items[0].id;

  const link = await recruiter.post(`/api/applications/${id}/booking-link`);
  expect(link.status(), await link.text()).toBe(201);
  const token = ((await link.json()) as { token: string }).token;
  const publicApi = await newRequest.newContext({ baseURL: BASE_URL });
  const booked = await publicApi.post(`/api/public/booking/${encodeURIComponent(token)}`, { data: { slot_id: slotId } });
  expect(booked.status(), await booked.text()).toBe(201);
  await publicApi.dispose();

  await logIn(page, PUEBLA_RECRUITER);
  await page.goto("/today");
  const interviews = page.getByRole("region", { name: "Interviews today" });
  await expect(interviews.getByRole("status")).toHaveCount(0);
  const link$ = interviews.locator(`a[href="/applications/${id}"]`);
  // The same rule as the page: the Slot's day in the Location's zone against the sim clock's day there.
  if (dayIn(start, timeZone) === dayIn(now, timeZone)) {
    await expect(link$).toHaveCount(1);
    await expect(interviews.getByRole("row").filter({ has: page.locator(`a[href="/applications/${id}"]`) })).toContainText(/Sazón Puebla/);
  } else {
    await expect(link$).toHaveCount(0);
  }
  // The clock was only read, never moved.
  const after = (await (await recruiter.get("/api/sim/clock")).json()) as { now: string };
  expect(new Date(after.now).getTime() - now.getTime()).toBeLessThan(5 * 60_000);

  await recruiter.dispose();
  await chat.close();
});

test.describe("Today and the Application page: accessibility, keyboard and widths", () => {
  let recruiter: APIRequestContext;
  let c: Screened;
  let d: Screened;

  test.beforeAll(async () => {
    recruiter = await apiAs(PUEBLA_RECRUITER);
    [c, d] = await Promise.all([qualified(recruiter), qualified(recruiter)]);
  });

  test.afterAll(async () => {
    await recruiter.dispose();
    await c.chat.close();
    await d.chat.close();
  });

  test("Today and the Application page have no axe violations", async ({ page }) => {
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto("/today");
    await expect(todayRow(page, c.id)).toHaveCount(1);
    await expectNoA11yViolations(page);
    await page.goto(`/applications/${c.id}`);
    await expect(page.getByRole("region", { name: "Actions" }).getByRole("button", { name: "Approve" })).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("Tab reaches every action on Today and on the Application page", async ({ page }) => {
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto("/today");
    await expect(todayRow(page, c.id)).toHaveCount(1);
    const reached = await tabThrough(page, {
      "Candidate link": (f) => f.href === `/applications/${c.id}` && f.tag === "a",
      "Approve": (f) => f.tag === "button" && f.name === "Approve" && f.rowHref === `/applications/${c.id}`,
    });
    expect(reached["Candidate link"]).toBeLessThan(reached["Approve"]);

    await page.goto(`/applications/${d.id}`);
    await expect(page.getByRole("region", { name: "Actions" }).getByRole("button", { name: "Approve" })).toBeVisible();
    await tabThrough(page, {
      "Claim": (f) => f.tag === "button" && f.name === "Claim",
      "Approve": (f) => f.tag === "button" && f.name === "Approve",
      "Start Takeover": (f) => f.tag === "button" && f.name === "Start Takeover",
    });
    // The focused button is Start Takeover: Enter starts it, and Tab then reaches Hand back.
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Takeover" })).toContainText("You hold this chat.");
    await tabThrough(page, { "Hand back": (f) => f.tag === "button" && f.name === "Hand back to the Screening Agent" }, 60, false);
  });

  for (const width of [768, 1440, 1920]) {
    test(`no sideways scroll at ${width}px${width === 768 ? ", and Approve stays in view" : ""}`, async ({ page }) => {
      mkdirSync(shotsDir, { recursive: true });
      await page.setViewportSize({ width, height: 900 });
      await logIn(page, PUEBLA_RECRUITER);
      const sideways = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

      await page.goto("/today");
      const row = todayRow(page, c.id);
      await expect(row).toHaveCount(1);
      expect(await sideways(), `Today at ${width}px`).toBe(true);
      if (width === 768) {
        const approve = row.getByRole("button", { name: "Approve" });
        await approve.scrollIntoViewIfNeeded();
        await expect(approve).toBeInViewport();
      }
      if (width === 1440) {
        // Above 1024px Takeovers heads the second column, so it starts within the first screen.
        const box = await page.getByRole("region", { name: "Takeovers waiting on me" }).boundingBox();
        expect(box?.y ?? Infinity, "the Takeovers section starts in the first viewport").toBeLessThan(900);
      }
      await page.screenshot({ path: join(shotsDir, `today-${width}.png`), fullPage: true });

      await page.goto(`/applications/${c.id}`);
      const approve = page.getByRole("region", { name: "Actions" }).getByRole("button", { name: "Approve" });
      await expect(approve).toBeVisible();
      expect(await sideways(), `the Application page at ${width}px`).toBe(true);
      if (width === 768) await expect(approve).toBeInViewport();
      await page.screenshot({ path: join(shotsDir, `detail-${width}.png`), fullPage: true });
    });
  }
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("a changed Stage is marked, but nothing animates", async ({ page }) => {
    const recruiter = await apiAs(PUEBLA_RECRUITER);
    const { chat, id } = await qualified(recruiter);
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto(`/applications/${id}`);
    const actions = page.getByRole("region", { name: "Actions" });
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
    await expect(page.locator("[data-emphasis]")).toHaveCount(0);

    await actions.getByRole("button", { name: "Approve" }).click();
    const mark = page.locator("[data-emphasis]");
    await expect(mark).toHaveCount(1);
    await expect(mark).toContainText(/Approved|Interview/);
    const style = await mark.evaluate((el) => {
      const s = getComputedStyle(el);
      return { animationName: s.animationName, transitionDuration: s.transitionDuration };
    });
    expect(style.animationName).toBe("none");
    expect(style.transitionDuration).toBe("0s");

    await recruiter.dispose();
    await chat.close();
  });
});
