// The Candidate's booking page (/book/:token) in a real browser on the sim stack (stub model, no real
// model call): no sideways scroll at 360 and 390, 44 px targets, the 390 px layout, the desktop column,
// the invalid-link state, axe in both themes, the keyboard path, and the token staying out of requests
// and the console. A token comes from a Candidate who screens to Qualified, an Approval and a booking link.
import { expect, test, type Locator, type Page, type Request } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { apiAs, applicationFor, BASE_URL, Candidate, OPERATOR, requireStack, screeningAnswers } from "./support.ts";

requireStack();

const PHONE_360 = { width: 360, height: 800 };
const PHONE_390 = { width: 390, height: 844 };
const PHONES = [PHONE_360, PHONE_390];
const TARGET = 44;

/** A token for a fresh Approved Application. Each test that books takes its own, because a booking
 * takes a slot from the shared stack. The page may open with a booking already made (the agent can book
 * through the approved turn), which the tests below allow for. */
async function bookingToken(): Promise<string> {
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
    return token;
  } finally {
    await api.dispose();
    await candidate.close();
  }
}

/** The token with one character inside its signature changed: the server must answer 404. */
function tampered(token: string): string {
  const at = token.length - 6;
  return token.slice(0, at) + (token[at] === "A" ? "B" : "A") + token.slice(at + 1);
}

const slots = (page: Page) => page.locator("fieldset label");
const confirmButton = (page: Page) => page.getByRole("button", { name: "Confirmar entrevista" });
const confirmation = (page: Page) => page.locator("section[role=status]");

async function openPicker(page: Page, token: string): Promise<void> {
  await page.goto(`/book/${token}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(slots(page).first()).toBeVisible();
}

async function openInvalid(page: Page, token: string): Promise<void> {
  await page.goto(`/book/${tampered(token)}`);
  await expect(page.getByRole("alert")).toBeVisible();
}

async function bookFirstSlot(page: Page): Promise<void> {
  await slots(page).first().click();
  await confirmButton(page).click();
  await expect(confirmation(page)).toBeVisible();
}

/** How far the page runs past the window's width; zero or less means no sideways scroll. */
async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

async function expectTargets(page: Page): Promise<void> {
  const targets: Locator[] = [
    ...(await page.getByRole("button").all()),
    ...(await page.getByRole("link").all()),
    ...(await slots(page).all()),
  ];
  expect(targets.length).toBeGreaterThan(0);
  for (const target of targets) {
    const box = await target.boundingBox();
    const name = (await target.innerText()).trim();
    expect(box, `"${name}" has a box`).not.toBeNull();
    expect(box?.width, `"${name}" is ${box?.width} px wide`).toBeGreaterThanOrEqual(TARGET);
    expect(box?.height, `"${name}" is ${box?.height} px high`).toBeGreaterThanOrEqual(TARGET);
  }
}

/** Lengthen every address and Location name in the real GET response, for the overflow check. */
async function lengthenAddresses(page: Page, token: string): Promise<void> {
  const long = "Avenida-Insurgentes-Sur-numero-1234567890-interior-45-Colonia-Roma-Norte-Alcaldia-Cuauhtemoc-Ciudad-de-Mexico";
  await page.route(
    (url) => url.pathname === `/api/public/booking/${token}`,
    async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      const response = await route.fetch();
      const body = await response.json();
      body.slots = body.slots.map((s: { location_name: string; address?: string | null }) => ({
        ...s,
        location_name: `${s.location_name} ${long}`,
        address: `${s.address ?? ""} ${long} ${long}`.trim(),
      }));
      return route.fulfill({ response, json: body });
    },
  );
}

test.describe("the page as it opens", () => {
  let token = "";
  test.beforeAll(async () => {
    token = await bookingToken();
  });

  for (const size of PHONES) {
    test(`has no sideways scroll at ${size.width}×${size.height}, in the picker, with a long address, and on a tampered link`, async ({ page }) => {
      await page.setViewportSize(size);
      await openPicker(page, token);
      expect(await overflow(page)).toBeLessThanOrEqual(0);

      await lengthenAddresses(page, token);
      await openPicker(page, token);
      await expect(page.getByText(/Avenida-Insurgentes-Sur/).first()).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);

      await openInvalid(page, token);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });

    test(`makes every button and slot at least ${TARGET} px at ${size.width}×${size.height}`, async ({ page }) => {
      await page.setViewportSize(size);
      await openPicker(page, token);
      expect(await slots(page).count()).toBeGreaterThan(0);
      await expectTargets(page);
    });
  }

  test("is polished at 390×844: Confirm in view, one button, 16 px text", async ({ page }) => {
    await page.setViewportSize(PHONE_390);
    await openPicker(page, token);
    await expect(confirmButton(page)).toBeInViewport({ ratio: 1 });
    await expect(page.getByRole("button")).toHaveCount(1);
    const small = await page.evaluate(() =>
      [...document.querySelectorAll("main p, main legend, main label, main label strong")]
        .map((el) => ({ text: (el.textContent ?? "").trim().slice(0, 40), size: parseFloat(getComputedStyle(el).fontSize) }))
        .filter((e) => e.size < 16),
    );
    expect(small).toEqual([]);
    expect(await page.locator("fieldset").count()).toBeGreaterThan(0);
    expect(await page.locator("fieldset legend").count()).toBe(await page.locator("fieldset").count());
  });

  for (const size of [
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
  ]) {
    test(`is one centred column no wider than 640 px at ${size.width}×${size.height}, with Confirm in the flow`, async ({ page }) => {
      await page.setViewportSize(size);
      await openPicker(page, token);
      const box = await page.getByRole("main").boundingBox();
      expect(box).not.toBeNull();
      expect(box?.width).toBeLessThanOrEqual(640);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
      const left = box?.x ?? 0;
      const right = clientWidth - left - (box?.width ?? 0);
      expect(Math.abs(left - right)).toBeLessThanOrEqual(1);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      const position = await confirmButton(page).evaluate((el) => getComputedStyle(el.parentElement as HTMLElement).position);
      expect(position).not.toBe("sticky");
      expect(position).not.toBe("fixed");
    });
  }

  test("keeps the Confirm bar to a fifth of a 360×800 screen, with the hint and with a notice", async ({ page }) => {
    await page.setViewportSize(PHONE_360);
    await openPicker(page, token);
    const bar = confirmButton(page).locator("..");
    const limit = PHONE_360.height * 0.2;
    expect((await bar.boundingBox())?.height).toBeLessThanOrEqual(limit);

    // A chosen time: the hint takes at most two lines and shows the whole Location.
    await slots(page).first().click();
    const hint = page.locator("[aria-live=polite]");
    await expect(hint).toContainText("Tu horario:");
    const line = await hint.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
    expect((await hint.boundingBox())?.height).toBeLessThanOrEqual(line * 2.1);
    // Nothing is cut: the text fits its box across and down, so the whole Location shows.
    const fit = await hint.evaluate((el) => ({ across: el.scrollWidth - el.clientWidth, down: el.scrollHeight - el.clientHeight }));
    expect(fit.across).toBeLessThanOrEqual(0);
    expect(fit.down).toBeLessThanOrEqual(1);
    expect((await bar.boundingBox())?.height).toBeLessThanOrEqual(limit);

    // A taken time: the notice takes the hint's place and the bar still fits.
    await page.route(
      (url) => url.pathname === `/api/public/booking/${token}`,
      (route) =>
        route.request().method() === "POST"
          ? route.fulfill({ status: 409, json: { error: "slot_taken", message: "taken" } })
          : route.fallback(),
    );
    await confirmButton(page).click();
    await expect(page.getByRole("alert")).toContainText("Ese horario se acaba de ocupar");
    expect((await bar.boundingBox())?.height).toBeLessThanOrEqual(limit);
  });

  for (const size of [{ width: 360, height: 800 }, { width: 768, height: 1024 }, { width: 1440, height: 900 }]) {
    test(`draws the rule between Locations above the legend, and fills the column with chips, at ${size.width}×${size.height}`, async ({ page }) => {
      await page.setViewportSize(size);
      await openPicker(page, token);
      const fieldsets = page.locator("fieldset");
      expect(await fieldsets.count()).toBeGreaterThan(1);
      // The second group's border box starts above its legend: the rule no longer runs through the name.
      const group = await fieldsets.nth(1).boundingBox();
      const legend = await fieldsets.nth(1).locator("legend").boundingBox();
      expect(group && legend).toBeTruthy();
      expect(legend?.y, "legend sits below the fieldset's top border").toBeGreaterThanOrEqual((group?.y ?? 0) + 1);
      // The first day's chips run to the column's edge.
      const edges = await fieldsets.first().evaluate((el) => {
        const labels = [...el.querySelectorAll("label")].filter((l) => l.parentElement === el.querySelector("label")?.parentElement);
        const grid = labels[0].parentElement as HTMLElement;
        return { chip: labels[labels.length - 1].getBoundingClientRect().right, grid: grid.getBoundingClientRect().right, group: el.getBoundingClientRect().right };
      });
      expect(Math.abs(edges.chip - edges.grid)).toBeLessThanOrEqual(2);
      expect(Math.abs(edges.chip - edges.group)).toBeLessThanOrEqual(4);
    });
  }

  test("answers a tampered link with the invalid state, in both languages, and no Retry", async ({ page }) => {
    await page.setViewportSize(PHONE_360);
    const response = await page.request.get(`/api/public/booking/${tampered(token)}`);
    expect(response.status()).toBe(404);
    await openInvalid(page, token);
    const alert = page.getByRole("alert");
    await expect(alert.locator('[lang="es"]')).toContainText("Este enlace no es válido");
    await expect(alert.locator('[lang="en"]')).toContainText("This link is not valid");
    await expect(page.getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("radio")).toHaveCount(0);
  });

  test("has no accessibility violations in the picker, in both themes", async ({ page }) => {
    await page.setViewportSize(PHONE_360);
    await openPicker(page, token);
    await expectNoA11yViolations(page);
  });

  test("has no accessibility violations in the invalid state, in both themes", async ({ page }) => {
    await page.setViewportSize(PHONE_360);
    await openInvalid(page, token);
    await expectNoA11yViolations(page);
  });

  test("asks for no referrer, and its own requests carry no Referer", async ({ page }) => {
    // Document.referrerPolicy is not a browser property, so the policy is read from the tag and proved
    // by what the browser sends: an API request the page makes after the tag is in place has no Referer.
    const api: Request[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) api.push(request);
    });
    await openPicker(page, token);
    expect(await page.locator('head meta[name="referrer"]').getAttribute("content")).toBe("no-referrer");
    expect(await page.locator('head meta[name="robots"]').getAttribute("content")).toBe("noindex");
    expect(api.length).toBeGreaterThan(0);
    for (const request of api) expect((await request.allHeaders()).referer).toBeUndefined();
  });
});

test.describe("after booking", () => {
  for (const size of PHONES) {
    test(`has no sideways scroll and 44 px targets in the confirmation and the already-booked picker at ${size.width}×${size.height}`, async ({ page }) => {
      const token = await bookingToken();
      await page.setViewportSize(size);
      await openPicker(page, token);
      await bookFirstSlot(page);
      await expect(page.getByRole("link", { name: "Añadir a mi calendario (.ics)" })).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      await expectTargets(page);

      // The same link again: the Candidate now has an Interview, and the page says so.
      await openPicker(page, token);
      await expect(page.getByText(/Ya tienes una entrevista/)).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      await expectTargets(page);
    });
  }

  test("has no accessibility violations in the confirmation, in both themes", async ({ page }) => {
    const token = await bookingToken();
    await page.setViewportSize(PHONE_360);
    await openPicker(page, token);
    await bookFirstSlot(page);
    await expectNoA11yViolations(page);
  });

  test("books by keyboard, moves focus to the confirmation, and leaks the token nowhere", async ({ page }) => {
    const token = await bookingToken();
    const origins = new Set<string>();
    const logs: string[] = [];
    page.on("request", (request) => {
      if (request.url().startsWith("http")) origins.add(new URL(request.url()).origin);
    });
    page.on("console", (message) => logs.push(message.text()));
    page.on("pageerror", (error) => logs.push(error.message));

    await page.setViewportSize(PHONE_390);
    await openPicker(page, token);

    const focusShowsRing = () =>
      page.evaluate(() => {
        const style = getComputedStyle(document.activeElement as Element);
        return style.outlineStyle !== "none" || style.boxShadow !== "none";
      });

    // Tab reaches the slot group, Space picks, Tab reaches Confirm, Enter books.
    await page.keyboard.press("Tab");
    await expect(slots(page).first().locator("input")).toBeFocused();
    expect(await page.evaluate(() => (document.activeElement as HTMLInputElement).closest("fieldset") !== null)).toBe(true);
    expect(await focusShowsRing()).toBe(true);
    await page.keyboard.press("Space");
    await expect(slots(page).first().locator("input")).toBeChecked();
    await page.keyboard.press("Tab");
    await expect(confirmButton(page)).toBeFocused();
    expect(await focusShowsRing()).toBe(true);
    await page.keyboard.press("Enter");

    await expect(confirmation(page)).toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.querySelector("section[role=status]"))).toBe(true);
    expect(await focusShowsRing()).toBe(true);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Añadir a mi calendario (.ics)" })).toBeFocused();
    expect(await focusShowsRing()).toBe(true);

    // The token stays on this origin and out of the console.
    expect([...origins]).toEqual([new URL(BASE_URL).origin]);
    expect(logs.filter((line) => line.includes(token))).toEqual([]);
  });

  test("keeps a focused slot clear of the sticky Confirm bar", async ({ page }) => {
    const token = await bookingToken();
    await page.setViewportSize(PHONE_390);
    await openPicker(page, token);
    const radios = page.getByRole("radio");
    const count = await radios.count();
    expect(count).toBeGreaterThan(0);
    const bar = confirmButton(page).locator("..");
    for (const index of new Set([count - 1, Math.max(0, count - 4), Math.floor(count / 2)])) {
      await radios.nth(index).focus();
      await expect(radios.nth(index)).toBeFocused();
      const slot = await radios.nth(index).locator("..").boundingBox();
      const barBox = await bar.boundingBox();
      expect(slot).not.toBeNull();
      expect(barBox).not.toBeNull();
      if (slot && barBox) {
        const apart = slot.y + slot.height <= barBox.y || slot.y >= barBox.y + barBox.height;
        expect(apart, `slot ${index} of ${count} at y ${slot.y}-${slot.y + slot.height} against the bar at ${barBox.y}-${barBox.y + barBox.height}`).toBe(true);
      }
    }
  });
});
