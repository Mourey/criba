// A screenshot of every route at five widths, as the role that can reach it, on the sim stack (stub
// model, no real model call). Each route must answer below 400 and show its landmark; how it lays out
// is other surfaces' business, so overflow is recorded as an annotation and never asserted. A guard
// fails when a static path in src/routes.tsx is in neither the route list nor the exclusion list.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import { apiAs, applicationFor, BASE_URL, Candidate, logIn, OPERATOR, PUEBLA_RECRUITER, requireStack, screeningAnswers } from "./support.ts";

requireStack();

const WIDTHS = [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

interface Link {
  token: string;
  applicationId: string;
}

/** One visit: the route pattern in src/routes.tsx it covers (null for a path no route names, the catch-all),
 * the path to open, the name of its shot, and the heading it must show when `h1, main` is too loose. */
interface Visit {
  pattern: string | null;
  path: (link: Link) => string;
  label: string;
  heading?: string;
}

function tampered(token: string): string {
  const at = token.length - 6;
  return token.slice(0, at) + (token[at] === "A" ? "B" : "A") + token.slice(at + 1);
}

// Anonymous: the public routes. /book/<tampered> is the same route as /book/<token>, in its invalid state.
const ANONYMOUS: Visit[] = [
  { pattern: "/login", path: () => "/login", label: "login" },
  { pattern: "/test-chat", path: () => "/test-chat", label: "test-chat" },
  { pattern: "/voice", path: () => "/voice", label: "voice" },
  { pattern: "/book/:token", path: (link) => `/book/${link.token}`, label: "book" },
  { pattern: "/book/:token", path: (link) => `/book/${tampered(link.token)}`, label: "book-invalid" },
];
// A Recruiter. The unknown path is here, not anonymous: NotFound sits inside RequireAuth, so a visitor
// who is not logged in is sent to /login instead.
const RECRUITER: Visit[] = [
  { pattern: "/today", path: () => "/today", label: "today" },
  { pattern: "/applications", path: () => "/applications", label: "applications" },
  { pattern: "/applications/:id", path: (link) => `/applications/${link.applicationId}`, label: "application" },
  { pattern: "/queue", path: () => "/queue", label: "queue" },
  { pattern: "/slots", path: () => "/slots", label: "slots" },
  { pattern: "/results", path: () => "/results", label: "results" },
  { pattern: null, path: () => "/no-such-page", label: "not-found", heading: "Page not found" },
];
// An Operator (the stage is reached by its URL only).
const OPERATOR_VISITS: Visit[] = [
  { pattern: "/overview", path: () => "/overview", label: "overview" },
  { pattern: "/economics", path: () => "/economics", label: "economics" },
  { pattern: "/stage", path: () => "/stage", label: "stage" },
];
const VISITS = [...ANONYMOUS, ...RECRUITER, ...OPERATOR_VISITS];

// Static paths in src/routes.tsx that no visit covers on purpose.
const EXCLUDED: string[] = [];
// The Operator conversation list and view: conversations.spec.ts is their browser proof.
EXCLUDED.push("/conversations", "/conversations/:id");

/** Every static `path:` in a routes source, without the catch-all. */
function staticPaths(source: string): string[] {
  return [...source.matchAll(/\bpath:\s*"([^"]+)"/g)].map((m) => m[1]).filter((path) => path !== "*");
}

/** The static paths in a routes source that no visit and no exclusion covers: dropping a visit shows up here. */
function uncovered(source: string, visits: Visit[] = VISITS): string[] {
  const known = new Set<string>([...visits.flatMap((v) => (v.pattern ? [v.pattern] : [])), ...EXCLUDED]);
  return staticPaths(source).filter((path) => !known.has(path));
}

test.describe("the route list", () => {
  const source = () => readFileSync(join(import.meta.dirname, "../../src/routes.tsx"), "utf8");

  test("covers every static path in src/routes.tsx", () => {
    expect(staticPaths(source()).length).toBeGreaterThan(0);
    expect(uncovered(source())).toEqual([]);
  });

  test("visits 15 routes", () => {
    expect(VISITS).toHaveLength(15);
    expect(new Set(VISITS.map((v) => v.label)).size).toBe(15);
  });

  test("reports a path that no list covers", () => {
    const sample = source().replace("export const routes", '{ path: "/an-extra-route" }\nexport const routes');
    expect(sample).not.toBe(source());
    expect(uncovered(sample)).toEqual(["/an-extra-route"]);
  });

  test("reports a route whose visit was dropped", () => {
    expect(uncovered(source(), VISITS.filter((v) => v.pattern !== "/stage"))).toEqual(["/stage"]);
  });
});

/** The token for a fresh Approved Application, and its id (the same helper as book.spec.ts: support.ts
 * is not this child's file). The Application may already hold a booking, which does not matter here. */
async function bookingLink(): Promise<Link> {
  const candidate = await Candidate.start("MX");
  const api = await apiAs(OPERATOR);
  try {
    await candidate.say("Hola, quiero trabajar de repartidor");
    for (const answer of screeningAnswers()) await candidate.say(answer);
    const app = await applicationFor(api, candidate.phone, "qualified");
    const detail = await (await api.get(`/api/applications/${app.id}`)).json();
    const item = (detail.open_queue_items as { id: string; kind: string }[]).find((i) => i.kind === "approval");
    if (!item) throw new Error(`Application ${app.id} has no open approval item: it cannot be approved`);
    const approved = await api.post(`/api/queue/approvals/${item.id}`, { data: { decision: "approved" } });
    expect(approved.ok(), await approved.text()).toBeTruthy();
    await expect
      .poll(async () => (await (await api.get(`/api/applications/${app.id}`)).json()).stage, { timeout: 30_000 })
      .toMatch(/^(approved|interview_booked)$/);
    const link = await api.post(`/api/applications/${app.id}/booking-link`);
    expect(link.status(), await link.text()).toBe(201);
    const { token } = (await link.json()) as { token: string };
    const page = await (await api.get(`/api/public/booking/${token}`)).json();
    if (page.slots.length === 0) throw new Error("the booking page lists no open slot: seed or reopen slots on this stack");
    return { token, applicationId: app.id };
  } finally {
    await api.dispose();
    await candidate.close();
  }
}

/** Visit one route and keep its screenshot. The file is written under the test's output directory
 * first, so it stays on disk (src/web/test-results/, ignored by git) once the test has passed. */
async function shoot(page: Page, testInfo: TestInfo, visit: Visit, link: Link, width: number): Promise<void> {
  const path = visit.path(link);
  const label = visit.label;
  const response = await page.goto(path);
  expect(response?.status(), `${label} answered ${response?.status()}`).toBeLessThan(400);
  // The page stayed where it was sent: a redirect (a visitor sent to /login) would shoot the wrong screen.
  expect(new URL(page.url()).pathname, `${label} stayed on its path`).toBe(new URL(path, BASE_URL).pathname);
  const landmark = visit.heading ? page.getByRole("heading", { level: 1, name: visit.heading }) : page.locator("h1, main").first();
  await expect(landmark, `${label} shows its landmark`).toBeVisible();
  await page.evaluate(() => document.fonts.ready.then(() => true));
  const name = `${label}@${width}`;
  const file = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  await testInfo.attach(name, { path: file, contentType: "image/png" });
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  testInfo.annotations.push({ type: "overflow", description: `${name}: ${sideways > 0 ? `${sideways} px past the window` : "none"}` });
}

async function signedIn(browser: Browser, width: number, height: number, email: string, landing: string): Promise<Page> {
  const context = await browser.newContext({ viewport: { width, height }, baseURL: BASE_URL });
  const page = await context.newPage();
  await logIn(page, email, { landing });
  return page;
}

test.describe("every route at five widths", () => {
  let link: Link;
  test.beforeAll(async () => {
    link = await bookingLink();
  });

  for (const { width, height } of WIDTHS) {
    test(`${width}×${height}`, async ({ browser }, testInfo) => {
      const anonymous = await browser.newContext({ viewport: { width, height }, baseURL: BASE_URL });
      const visitor = await anonymous.newPage();
      for (const visit of ANONYMOUS) await shoot(visitor, testInfo, visit, link, width);
      await anonymous.close();

      const recruiter = await signedIn(browser, width, height, PUEBLA_RECRUITER, "/today");
      for (const visit of RECRUITER) await shoot(recruiter, testInfo, visit, link, width);
      await recruiter.context().close();

      const operator = await signedIn(browser, width, height, OPERATOR, "/overview");
      for (const visit of OPERATOR_VISITS) await shoot(operator, testInfo, visit, link, width);
      await operator.context().close();
    });
  }
});
