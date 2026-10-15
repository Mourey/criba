// Shell browser proof (spec SA2, SA3, SA5, SA6, SA7, SA8, SA12) on the sim stack: landings, the route
// stubs and the full-bleed stage, the pre-paint theme, reduced motion, axe in both themes, keyboard
// focus, no sideways scroll at 768 and 1440, and the logIn helper. Nothing here writes to the
// shared database beyond logging in.
import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { logIn, OPERATOR, PUEBLA_RECRUITER, requireStack } from "./support.ts";

requireStack();

const RECRUITER = PUEBLA_RECRUITER;
const h1 = (page: Page) => page.getByRole("heading", { level: 1 });
const notFound = (page: Page) => page.getByRole("heading", { level: 1, name: "Page not found" });

test.describe("landing and the route stubs", () => {
  test("an Operator lands on Overview, and / goes there", async ({ page }) => {
    await logIn(page, OPERATOR);
    await page.goto("/");
    await expect(page).toHaveURL(/\/overview$/);
    await expect(h1(page)).toBeVisible();
    await expect(notFound(page)).toHaveCount(0);
  });

  test("a Recruiter lands on Today, and / goes there", async ({ page }) => {
    await logIn(page, RECRUITER);
    await page.goto("/");
    await expect(page).toHaveURL(/\/today$/);
    await expect(h1(page)).toBeVisible();
    await expect(notFound(page)).toHaveCount(0);
  });

  test("an Operator sees the demo stage full-bleed, with no Main navigation", async ({ page }) => {
    await logIn(page, OPERATOR);
    await page.goto("/stage");
    await expect(h1(page)).toBeVisible();
    await expect(notFound(page)).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  });

  test("a Recruiter at /stage sees Page not found", async ({ page }) => {
    await logIn(page, RECRUITER);
    await page.goto("/stage");
    await expect(notFound(page)).toBeVisible();
  });

  test("an Operator previewing the Recruiter view at /stage sees Page not found", async ({ page }) => {
    await logIn(page, OPERATOR);
    await page.getByRole("button", { name: "Preview as Recruiter" }).click();
    await expect(page.getByRole("status")).toContainText("Previewing the Recruiter view");
    await page.goto("/stage");
    await expect(notFound(page)).toBeVisible();
  });
});

test.describe("theme", () => {
  test("a stored dark is set by the inline script alone, before the app's JavaScript", async ({ page }) => {
    await page.addInitScript(() => window.localStorage.setItem("criba.theme", "dark"));
    await page.route("**/assets/*.js", (route) => route.abort());
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    expect(await page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBe("dark");
  });

  test("with nothing stored, the system dark scheme decides and data-theme stays absent", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
    const colours = await page.evaluate(() => {
      // Resolve the ground token under each forced theme, so the test names no colour value.
      const root = document.documentElement;
      const attribute = root.getAttribute("data-theme");
      const body = getComputedStyle(document.body).backgroundColor;
      const resolve = (theme: string) => {
        root.setAttribute("data-theme", theme);
        const value = getComputedStyle(document.body).backgroundColor;
        root.removeAttribute("data-theme");
        return value;
      };
      return { attribute, body, dark: resolve("dark"), light: resolve("light") };
    });
    expect(colours.attribute).toBeNull();
    expect(colours.dark).not.toBe(colours.light);
    expect(colours.body).toBe(colours.dark);
  });

  test("the toggle changes data-theme and the choice survives a reload", async ({ page }) => {
    await logIn(page, OPERATOR);
    const toggle = page.getByRole("button", { name: /^Theme:/ });
    const theme = () => page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    const before = await theme();
    await toggle.click();
    await expect.poll(theme).not.toBe(before);
    await toggle.click();
    await expect.poll(theme).toBe("dark");
    await page.reload();
    await expect(page.getByRole("button", { name: /^Theme:/ })).toBeVisible();
    expect(await theme()).toBe("dark");
  });

  test("a throwing localStorage.getItem raises no page error", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() => {
      Storage.prototype.getItem = () => {
        throw new Error("storage blocked");
      };
    });
    await logIn(page, OPERATOR);
    await page.getByRole("button", { name: /^Theme:/ }).click();
    expect(errors).toEqual([]);
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  async function expectNothingMoves(page: Page) {
    // Let the route settle (lazy chunk, entering fade) before reading computed styles. Not
    // a network-idle wait: an open EventSource stream keeps a request in flight forever.
    await expect(h1(page)).toBeVisible();
    await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));
    const moving = await page.evaluate(() =>
      [...document.querySelectorAll("*")]
        .map((el) => ({ el, style: getComputedStyle(el) }))
        .filter(({ style }) => style.animationName !== "none" || style.transitionDuration !== "0s")
        .map(({ el, style }) => `${el.tagName.toLowerCase()}.${el.className}: ${style.animationName} / ${style.transitionDuration}`),
    );
    expect(moving).toEqual([]);
  }

  test("nothing animates on Login", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
    await expectNothingMoves(page);
  });

  test("nothing animates on the Recruiter's Today", async ({ page }) => {
    await logIn(page, RECRUITER);
    await page.goto("/today");
    await expect(h1(page)).toBeVisible();
    await expectNothingMoves(page);
  });
});

test.describe("accessibility", () => {
  test("Login has no axe violations in either theme", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("the Recruiter's Today and Page not found have no axe violations", async ({ page }) => {
    await logIn(page, RECRUITER);
    for (const path of ["/today", "/no-such-page", "/stage"]) {
      await page.goto(path);
      await expect(h1(page)).toBeVisible();
      await expectNoA11yViolations(page);
    }
  });

  test("the Operator's Overview, demo stage and Page not found have no axe violations", async ({ page }) => {
    await logIn(page, OPERATOR);
    for (const path of ["/overview", "/stage", "/no-such-page"]) {
      await page.goto(path);
      await expect(h1(page)).toBeVisible();
      await expectNoA11yViolations(page);
    }
  });

  test("the helper fails on white text on a near-white ground", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
    await page.evaluate(() => {
      const p = document.createElement("p");
      // White on near-white: axe reports an exact match of text and background as "incomplete"
      // (it cannot tell hidden text from unreadable text), so the injected text is barely off-white.
      p.textContent = "Text nobody can read";
      p.style.color = "white";
      p.style.background = "rgb(238, 238, 238)";
      document.querySelector("main")?.append(p);
    });
    await expect(expectNoA11yViolations(page)).rejects.toThrow(/color-contrast/);
  });

  test("Tab reaches every Shell control, each with a visible focus indicator", async ({ page }) => {
    await logIn(page, OPERATOR);
    await page.goto("/overview");
    await expect(h1(page)).toBeVisible();
    const wanted = ["Overview", "Applications", "Review queue", "Interview slots", "Results", "Economics", "Test chat", "Theme:", "Preview as Recruiter", "Log out"];
    const reached = new Map<string, boolean>();
    for (let i = 0; i < 40 && reached.size < wanted.length; i++) {
      await page.keyboard.press("Tab");
      const focused = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const style = getComputedStyle(el);
        const outlined = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
        return { name: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim(), visible: outlined || style.boxShadow !== "none" };
      });
      if (!focused) continue;
      const match = wanted.find((w) => (w.endsWith(":") ? focused.name.startsWith(w) : focused.name === w));
      if (match && !reached.has(match)) reached.set(match, focused.visible);
    }
    expect([...reached.keys()].sort()).toEqual([...wanted].sort());
    expect([...reached].filter(([, visible]) => !visible).map(([name]) => name)).toEqual([]);
  });

  for (const width of [768, 1440]) {
    test(`no sideways scroll at ${width}px on Login and Today`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
      await page.goto("/login");
      await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
      expect(await fits()).toBe(true);
      await logIn(page, RECRUITER);
      await page.goto("/today");
      await expect(h1(page)).toBeVisible();
      expect(await fits()).toBe(true);
    });
  }
});

test.describe("the logIn helper", () => {
  test("checks the landing from the address, or the one given", async ({ page }) => {
    await logIn(page, RECRUITER);
    await expect(page).toHaveURL(/\/applications$/);
    await page.context().clearCookies();
    await logIn(page, OPERATOR, { landing: "/overview" });
    await expect(page).toHaveURL(/\/applications$/);
  });

  test("fails when the landing does not match, and for an unknown address", async ({ page }) => {
    await expect(logIn(page, RECRUITER, { landing: /\/overview$/ })).rejects.toThrow();
    await expect(logIn(page, "someone@example.com")).rejects.toThrow(/no known landing/);
  });
});
