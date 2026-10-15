// The operator console's left navigation and full-width pages in a real browser (ui-shell, UI1 to UI3):
// the items per role, the 56px rail and the open column, the remembered choice, reduced motion, the
// right edge of the page at 1440 and 1920, the 1920px frame on a wider window, and axe in both
// navigation states on the three list pages. It only reads: no Candidate is created.
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { BASE_URL, logIn, OPERATOR, PUEBLA_RECRUITER, requireStack } from "./support.ts";

requireStack();

const OPERATOR_ITEMS = ["Overview", "Applications", "Review queue", "Interview slots", "Results", "Test chat", "Test voice", "Economics"];
const RECRUITER_ITEMS = ["Today", "Applications", "Review queue", "Interview slots", "Results", "Test chat", "Test voice"];
const RAIL_PX = 56;
const OPEN_PX = 224; // --nav-open, 14rem
const PAGE_PADDING_PX = 16; // --space-4
const COLUMN = "#shell-nav-column";
const TOGGLE = /^(Collapse|Expand) navigation$/;

interface Session {
  context: BrowserContext;
  page: Page;
}

/** A fresh signed-in browser at a size, so no earlier test's stored navigation choice leaks in. */
async function signIn(
  browser: Browser,
  email: string,
  size: { width: number; height: number },
  options: { reducedMotion?: "reduce" | "no-preference" } = {},
): Promise<Session> {
  const context = await browser.newContext({ baseURL: BASE_URL, viewport: size, ...options });
  const page = await context.newPage();
  await logIn(page, email);
  return { context, page };
}

async function withSession(
  browser: Browser,
  email: string,
  size: { width: number; height: number },
  body: (page: Page) => Promise<void>,
  options: { reducedMotion?: "reduce" | "no-preference" } = {},
): Promise<void> {
  const session = await signIn(browser, email, size, options);
  try {
    await body(session.page);
  } finally {
    await session.context.close();
  }
}

const mainLabels = (page: Page) => page.getByRole("navigation", { name: "Main" }).getByRole("link").allTextContents();

/** Waits for the column to stop animating, then returns its width. */
async function columnWidth(page: Page): Promise<number> {
  let width = 0;
  await expect
    .poll(
      async () => {
        const box = await page.locator(COLUMN).boundingBox();
        const next = box?.width ?? 0;
        const settled = next === width;
        width = next;
        return settled;
      },
      { intervals: [250], timeout: 10_000 },
    )
    .toBe(true);
  return width;
}

async function setNav(page: Page, want: "open" | "collapsed"): Promise<void> {
  const button = page.getByRole("button", { name: TOGGLE });
  const open = (await button.getAttribute("aria-expanded")) === "true";
  if (open !== (want === "open")) await button.click();
  await expect(button).toHaveAttribute("aria-expanded", String(want === "open"));
}

async function listReady(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".ui-skeleton")).toHaveCount(0);
}

test.describe("items per role at 1920x1080", () => {
  const size = { width: 1920, height: 1080 };

  test("an Operator sees Overview first and Economics last", async ({ browser }) => {
    await withSession(browser, OPERATOR, size, async (page) => {
      expect(await mainLabels(page)).toEqual(OPERATOR_ITEMS);
    });
  });

  test("a Recruiter sees Today first and no Economics", async ({ browser }) => {
    await withSession(browser, PUEBLA_RECRUITER, size, async (page) => {
      expect(await mainLabels(page)).toEqual(RECRUITER_ITEMS);
    });
  });

  test("an Operator previewing the Recruiter view sees the Recruiter's items", async ({ browser }) => {
    await withSession(browser, OPERATOR, size, async (page) => {
      await page.getByRole("button", { name: "Preview as Recruiter" }).click();
      await expect(page.getByRole("status")).toContainText("Previewing the Recruiter view");
      expect(await mainLabels(page)).toEqual(RECRUITER_ITEMS);
    });
  });
});

test.describe("the column's width", () => {
  test("is the 56px rail at 1280 by default", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 1280, height: 720 }, async (page) => {
      await expect(page.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
      expect(Math.abs((await columnWidth(page)) - RAIL_PX)).toBeLessThanOrEqual(1);
    });
  });

  test("is the 56px rail at 767 even after choosing open", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 767, height: 900 }, async (page) => {
      expect(Math.abs((await columnWidth(page)) - RAIL_PX)).toBeLessThanOrEqual(1);
      await setNav(page, "open");
      expect(Math.abs((await columnWidth(page)) - RAIL_PX)).toBeLessThanOrEqual(1);
      expect(await page.evaluate(() => window.localStorage.getItem("criba.nav"))).toBe("open");
    });
  });

  test("is open (14rem) at 1440 by default", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 1440, height: 900 }, async (page) => {
      await expect(page.getByRole("button", { name: "Collapse navigation" })).toHaveAttribute("aria-expanded", "true");
      expect(Math.abs((await columnWidth(page)) - OPEN_PX)).toBeLessThanOrEqual(1);
    });
  });
});

test.describe("the remembered choice", () => {
  test("a collapse at 1440 survives a reload", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 1440, height: 900 }, async (page) => {
      await setNav(page, "collapsed");
      await page.reload();
      await expect(page.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
      expect(Math.abs((await columnWidth(page)) - RAIL_PX)).toBeLessThanOrEqual(1);
    });
  });

  test("an expand at 1280 survives a reload", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 1280, height: 720 }, async (page) => {
      await setNav(page, "open");
      await page.reload();
      await expect(page.getByRole("button", { name: "Collapse navigation" })).toHaveAttribute("aria-expanded", "true");
      expect(Math.abs((await columnWidth(page)) - OPEN_PX)).toBeLessThanOrEqual(1);
    });
  });
});

test.describe("motion", () => {
  // logIn() returns on the URL, so the frame and the column may not have rendered yet under load.
  const durations = async (page: Page) => {
    await expect(page.locator(".shell-frame")).toBeVisible();
    await expect(page.locator(COLUMN)).toBeVisible();
    return page.evaluate((column) => ({
      frame: getComputedStyle(document.querySelector(".shell-frame") as Element).transitionDuration,
      column: getComputedStyle(document.querySelector(column) as Element).transitionDuration,
    }), COLUMN);
  };

  test("nothing in the frame or the column transitions under reduced motion", async ({ browser }) => {
    await withSession(
      browser,
      OPERATOR,
      { width: 1440, height: 900 },
      async (page) => {
        const found = await durations(page);
        expect(found.frame).toBe("0s");
        expect(found.column).toBe("0s");
      },
      { reducedMotion: "reduce" },
    );
  });

  test("the frame does transition without it, so the check above is not vacuous", async ({ browser }) => {
    await withSession(
      browser,
      OPERATOR,
      { width: 1440, height: 900 },
      async (page) => {
        expect((await durations(page)).frame).not.toBe("0s");
      },
      { reducedMotion: "no-preference" },
    );
  });
});

test.describe("full-width pages", () => {
  for (const size of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
  ]) {
    test(`the heading row and the table reach the window's right padding at ${size.width}x${size.height}`, async ({ browser }) => {
      await withSession(browser, OPERATOR, size, async (page) => {
        await setNav(page, "open");
        await listReady(page);
        await columnWidth(page);
        const edges = await page.evaluate(() => {
          const right = (el: Element | null) => (el ? window.innerWidth - el.getBoundingClientRect().right : Number.NaN);
          return {
            head: right(document.querySelector(".page-head")),
            region: right(document.querySelector('[role="region"][aria-label="Applications table"]')),
            scroll: document.documentElement.scrollWidth,
            client: document.documentElement.clientWidth,
          };
        });
        for (const gap of [edges.head, edges.region]) {
          expect(gap).toBeGreaterThanOrEqual(-0.5);
          expect(gap).toBeLessThanOrEqual(PAGE_PADDING_PX + 1);
        }
        expect(edges.scroll).toBeLessThanOrEqual(edges.client);
      });
    });
  }

  test("on a 2400px window the frame is 1920px wide at the left edge and the ground covers the window", async ({ browser }) => {
    await withSession(browser, OPERATOR, { width: 2400, height: 1080 }, async (page) => {
      await setNav(page, "open");
      await listReady(page);
      const boxes = await page.evaluate(() => {
        const box = (selector: string) => {
          const rect = (document.querySelector(selector) as Element).getBoundingClientRect();
          return { x: rect.x, width: rect.width };
        };
        return { frame: box(".shell-frame"), ground: box(".shell"), inner: window.innerWidth };
      });
      expect(boxes.frame.width).toBe(1920);
      expect(boxes.frame.x).toBe(0);
      expect(boxes.ground.x).toBe(0);
      expect(boxes.ground.width).toBe(boxes.inner);
      expect(boxes.inner).toBe(2400);
    });
  });
});

test.describe("axe, both themes, in both navigation states", () => {
  const pages = [
    { name: "Applications", path: "/applications" },
    { name: "Rejections", path: "/queue?tab=rejection" },
    { name: "Interview slots", path: "/slots" },
  ] as const;
  for (const target of pages) {
    for (const state of ["open", "collapsed"] as const) {
      test(`${target.name}, navigation ${state}`, async ({ browser }) => {
        await withSession(browser, OPERATOR, { width: 1920, height: 1080 }, async (page) => {
          await page.goto(target.path);
          await setNav(page, state);
          await listReady(page);
          await columnWidth(page);
          await expectNoA11yViolations(page);
        });
      });
    }
  }
});
