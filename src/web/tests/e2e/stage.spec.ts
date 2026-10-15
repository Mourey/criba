// Demo stage browser proof (spec stage-panes SP1, SP2, SP5, SP7-SP11, SP13, SP14) on the sim stack: the
// gate and its chunk, the clock region, a Take played to Qualified with the stub model, the record
// pane beside it, both views without cost, 1920x1080 without scroll, reduced motion, axe in both
// themes and a keyboard pass. The Candidate types the persona's own words (screeningAnswers()).
import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { logIn, OPERATOR, PUEBLA_RECRUITER, requireStack, screeningAnswers } from "./support.ts";

requireStack();

test.use({ viewport: { width: 1920, height: 1080 } });

const PANES_CHUNK = /StageScreen-/;

const phonePane = (page: Page) => page.getByRole("region", { name: "Phone" });
const recordPane = (page: Page) => page.getByRole("region", { name: "Record" });
const composer = (page: Page) => phonePane(page).getByRole("textbox", { name: "Message" });
const sendButton = (page: Page) => phonePane(page).getByRole("button", { name: "Send" });
const field = (page: Page, name: string) => page.locator(`[data-field="${name}"]`);

async function openStage(page: Page): Promise<void> {
  await logIn(page, OPERATOR, { landing: "/overview" });
  await page.goto("/stage");
  await expect(phonePane(page)).toBeVisible();
}

async function startTake(page: Page): Promise<void> {
  await phonePane(page).getByRole("button", { name: "Start conversation" }).click();
  await expect(phonePane(page).getByRole("log", { name: "Messages" })).toBeVisible();
}

/** One Candidate message typed and sent, then the agent's turn waited out (a new agent bubble, then "online"). */
async function say(page: Page, text: string): Promise<void> {
  const phone = phonePane(page);
  const agent = phone.locator('[data-side="left"]');
  const before = await agent.count();
  await composer(page).fill(text);
  await sendButton(page).click();
  await expect(phone.locator('[data-side="right"]').filter({ hasText: text })).toBeVisible();
  await expect(agent).not.toHaveCount(before, { timeout: 60_000 });
  await expect(phone.getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });
}

/** The document does not scroll in either axis. */
async function expectNoPageScroll(page: Page): Promise<void> {
  const sizes = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(sizes.scrollHeight).toBeLessThanOrEqual(sizes.innerHeight);
  expect(sizes.scrollWidth).toBeLessThanOrEqual(sizes.innerWidth);
}

/** The inactive tab panel takes no space, and the active one starts right under the tab list and the facts under it. */
async function expectTabsTight(page: Page): Promise<void> {
  const inactive = page.locator('.record [role="tabpanel"][hidden]');
  for (const box of await inactive.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height))) expect(box).toBe(0);
  const gaps = await page.evaluate(() => {
    const list = document.querySelector('.record [role="tablist"]')?.getBoundingClientRect();
    const panel = document.querySelector('.record [role="tabpanel"]:not([hidden])')?.getBoundingClientRect();
    const facts = document.querySelector('[data-testid="record-facts"]')?.getBoundingClientRect();
    if (!list || !panel || !facts) return null;
    return { panel: panel.top - list.bottom, facts: facts.top - list.bottom };
  });
  expect(gaps).not.toBeNull();
  expect(Math.abs(gaps?.panel ?? 99)).toBeLessThanOrEqual(2);
  expect(gaps?.facts ?? 99).toBeLessThanOrEqual(32);
}

/** The record pane's content width: its client width less its padding. */
const contentWidth = (page: Page) =>
  recordPane(page).evaluate((el) => {
    const style = getComputedStyle(el);
    return el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  });

/** The width of a region inside the record pane. */
async function widthOf(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box?.width ?? 0;
}

/** Presses Tab from the top of the page until `target` has focus. */
async function tabTo(page: Page, target: Locator, maxTabs = 80): Promise<void> {
  await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
  for (let i = 0; i < maxTabs; i += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((el) => el === document.activeElement)) return;
  }
  throw new Error(`Tab did not reach ${target} in ${maxTabs} presses`);
}

test.describe("gate", () => {
  test("a Recruiter at /stage sees Page not found and never requests the panes", async ({ page }) => {
    const requested: string[] = [];
    page.on("response", (response) => {
      if (PANES_CHUNK.test(response.url())) requested.push(response.url());
    });
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto("/stage");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(phonePane(page)).toHaveCount(0);
    await page.waitForLoadState("networkidle");
    expect(requested).toEqual([]);
  });

  test("an Operator at /stage gets the panes chunk, the Phone and Record regions and the Simulated clock", async ({ page }) => {
    const requested: string[] = [];
    page.on("response", (response) => {
      if (PANES_CHUNK.test(response.url())) requested.push(response.url());
    });
    await openStage(page);
    await expect(recordPane(page)).toBeVisible();
    await expect(page.getByRole("region", { name: "Simulated clock" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Demo stage" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
    expect(requested.length).toBeGreaterThan(0);
  });

  // Owner decision 2026-10-10 (fix lane sim-clock-lag): the strip read a minute behind the thread
  // because it showed the one fetched instant. The sim clock is real time plus an offset, so the page
  // moves it on by itself; the browser's clock is moved here, the fetch is not repeated.
  test("the Simulated clock moves on with time between fetches", async ({ page }) => {
    await page.clock.install();
    await openStage(page);
    const time = page.getByRole("region", { name: "Simulated clock" }).locator("time");
    await expect(time).toBeVisible();
    const before = Date.parse((await time.getAttribute("datetime")) as string);
    await page.clock.fastForward("03:00");
    await expect
      .poll(async () => Date.parse((await time.getAttribute("datetime")) as string) - before)
      .toBeGreaterThanOrEqual(170_000);
  });
});

test.describe("start card", () => {
  test("shows a Mexican phone, does not scroll at 1920x1080, and Start is reached and pressed by keyboard", async ({ page }) => {
    await openStage(page);
    await expect(phonePane(page).getByTestId("start-phone")).toHaveText(/^\+52[0-9]{10}$/);
    await expect(phonePane(page).getByLabel("Country")).toHaveValue("MX");
    await expect(recordPane(page)).toContainText("The Application appears here after the Candidate's first message");
    await expectNoPageScroll(page);

    await tabTo(page, phonePane(page).getByLabel("Country"));
    await tabTo(page, phonePane(page).getByRole("button", { name: "Start conversation" }));
    await page.keyboard.press("Enter");
    await expect(phonePane(page).getByRole("log", { name: "Messages" })).toBeVisible();
    await expect(composer(page)).toBeVisible();
  });

  test("has no accessibility violations in either theme", async ({ page }) => {
    await openStage(page);
    await expect(phonePane(page).getByRole("button", { name: "Start conversation" })).toBeVisible();
    await expectNoA11yViolations(page);
  });
});

test("a Take plays to Qualified beside its Application", async ({ page }) => {
  const [name, zone, license, ...rest] = screeningAnswers();
  await openStage(page);
  const phone = phonePane(page);
  const phoneNumber = (await phone.getByTestId("start-phone").textContent()) ?? "";
  expect(phoneNumber).toMatch(/^\+52/);
  await startTake(page);

  // The record is there within 5 s of the click on Send for the first message.
  await composer(page).fill("Hola, quiero trabajar de repartidor");
  await sendButton(page).click();
  await expect(field(page, "full_name")).toBeVisible({ timeout: 5000 });
  await expect(recordPane(page)).toContainText(phoneNumber);
  const agent = phone.locator('[data-side="left"]');
  await expect(agent.first()).toBeVisible({ timeout: 60_000 });
  await expect(phone.getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });

  await say(page, name);
  await say(page, zone);
  // The Zone answer is recorded: its row says Valid.
  await expect(field(page, "zone").getByRole("img", { name: "Valid" })).toBeVisible({ timeout: 30_000 });
  await expectNoPageScroll(page);

  // The driver's license question comes with buttons, live while it is the latest question.
  const licenseButton = phone.getByRole("button", { name: "Sí, tengo licencia" });
  await expect(licenseButton).toBeEnabled();
  await expectNoA11yViolations(page);

  // Keyboard: every reply button, then the composer and Send (typed answer, sent with Enter).
  for (const button of await phone.getByRole("group", { name: "Reply options" }).last().getByRole("button").all()) {
    await tabTo(page, button);
  }
  await tabTo(page, composer(page));
  await page.keyboard.type(license);
  await page.keyboard.press("Tab");
  await expect(sendButton(page)).toBeFocused();
  const before = await agent.count();
  await page.keyboard.press("Enter");
  await expect(phone.locator('[data-side="right"]').filter({ hasText: license })).toBeVisible();
  await expect(agent).not.toHaveCount(before, { timeout: 60_000 });
  await expect(phone.getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });
  // The next answer was typed, so the earlier buttons are no longer live.
  await expect(licenseButton).toBeDisabled();

  // Availability is typed; the preferred-schedule question that follows is a list ("Options").
  const [availability, , ...later] = rest;
  await say(page, availability);
  const optionsButton = phone.getByRole("button", { name: "Options" }).last();
  await expect(optionsButton).toBeEnabled();
  const sheet = phone.getByRole("dialog", { name: "Options" });
  await tabTo(page, optionsButton);
  await page.keyboard.press("Enter");
  await expect(sheet).toBeVisible();
  await expect(optionsButton).toHaveAttribute("aria-expanded", "true");
  // Focus moves into the sheet, which stays inside the phone frame and is not modal.
  await expect(sheet.getByRole("button").first()).toBeFocused();
  await expect(sheet).toHaveAttribute("aria-modal", "false");
  expect(await sheet.evaluate((el) => el.closest(".phone") !== null)).toBe(true);
  await expectNoA11yViolations(page);

  // Escape closes it and focus goes back to Options; Enter opens it again.
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(optionsButton).toBeFocused();
  await expect(optionsButton).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Enter");
  await expect(sheet).toBeVisible();

  // Pick "Flexible" by keyboard: the tap is sent as the option, and the sheet closes.
  const flexible = sheet.getByRole("button", { name: /flexible/i });
  await tabTo(page, flexible);
  const turns = await agent.count();
  await page.keyboard.press("Enter");
  await expect(sheet).toHaveCount(0);
  await expect(agent).not.toHaveCount(turns, { timeout: 60_000 });
  await expect(phone.getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });
  await expect(optionsButton).toBeDisabled();

  for (const answer of later) await say(page, answer);

  // Qualified: the stage badge, no page scroll, and the Agent's view fits the record pane.
  await expect(page.getByTestId("record-stage")).toContainText("Qualified", { timeout: 60_000 });
  await expectNoPageScroll(page);
  const fit = await recordPane(page).evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
  expect(fit.scrollHeight).toBeLessThanOrEqual(fit.clientHeight);
  await expectTabsTight(page);
  // The Agent's view: the Candidate's own words, a split with Answers beside Events, and no Summary.
  await expect(recordPane(page)).toContainText("tengo licencia");
  await expect(recordPane(page).getByRole("region", { name: "Summary" })).toHaveCount(0);
  expect(await widthOf(recordPane(page).getByRole("region", { name: "Answers" }))).toBeLessThan((await contentWidth(page)) * 0.75);

  // Both views, reached by keyboard, with no violations; the Recruiter's view has the summary and no cost.
  const agentTab = page.getByRole("tab", { name: "Agent's view" });
  const recruiterTab = page.getByRole("tab", { name: "Recruiter's view" });
  await tabTo(page, agentTab);
  await expect(agentTab).toHaveAttribute("aria-selected", "true");
  await expectNoA11yViolations(page);
  await agentTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(recruiterTab).toHaveAttribute("aria-selected", "true");
  await expect(recruiterTab).toBeFocused();
  await expect(recordPane(page).getByRole("region", { name: "Summary" })).toBeVisible({ timeout: 30_000 });
  await expectTabsTight(page);
  // The Recruiter's view has a different shape: the Summary spans the pane, the Answers are compact and
  // carry none of the Candidate's own words.
  const full = await contentWidth(page);
  expect(await widthOf(recordPane(page).getByRole("region", { name: "Summary" }))).toBeGreaterThanOrEqual(full * 0.95);
  expect(await widthOf(recordPane(page).getByRole("region", { name: "Answers" }))).toBeGreaterThanOrEqual(full * 0.95);
  expect(await recordPane(page).innerText()).not.toContain("tengo licencia");
  await expect(recordPane(page).getByRole("list", { name: "Waiting for a person" })).toContainText("Waiting for approval since");
  // What waits for a person leads: above the Summary, in the live treatment.
  const lead = recordPane(page).getByRole("list", { name: "Waiting for a person" });
  await expect(lead.locator(".ui-tag--live")).toBeVisible();
  const leadBox = await lead.boundingBox();
  const summaryBox = await recordPane(page).getByRole("region", { name: "Summary" }).boundingBox();
  expect(leadBox).not.toBeNull();
  expect(summaryBox).not.toBeNull();
  expect(leadBox?.y ?? Infinity).toBeLessThan(summaryBox?.y ?? 0);
  const fitRecruiter = await recordPane(page).evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
  expect(fitRecruiter.scrollHeight).toBeLessThanOrEqual(fitRecruiter.clientHeight);
  expect(await recordPane(page).innerText()).not.toMatch(/\$\d|tokens/i);
  await expectNoA11yViolations(page);
  await expectNoPageScroll(page);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("nothing in the phone or the record animates or transitions", async ({ page }) => {
    const [name, zone] = screeningAnswers();
    await openStage(page);
    await startTake(page);
    await composer(page).fill("Hola, quiero trabajar de repartidor");
    await sendButton(page).click();
    await expect(field(page, "full_name")).toBeVisible({ timeout: 5000 });
    await expect(phonePane(page).locator('[data-side="left"]').first()).toBeVisible({ timeout: 60_000 });
    await say(page, name);
    await say(page, zone);
    // After the Zone answer has changed its row.
    await expect(field(page, "zone").getByRole("img", { name: "Valid" })).toBeVisible({ timeout: 30_000 });

    const offenders = await page.evaluate(() =>
      [...document.querySelectorAll(".phone, .phone *, .record, .record *")]
        .filter((el) => {
          const style = getComputedStyle(el);
          return style.animationName !== "none" || style.transitionDuration.split(",").some((d) => d.trim() !== "0s");
        })
        .map((el) => `${el.tagName}.${el.className}`),
    );
    expect(offenders).toEqual([]);
    expect(await page.evaluate(() => document.querySelectorAll(".phone *, .record *").length)).toBeGreaterThan(20);
  });
});

// ---------------------------------------------------------------------------------------------
// The playback control (stage-playback PB2, PB5, PB7): personas played from the browser on the stub.
// ---------------------------------------------------------------------------------------------

const consolePanel = (page: Page) => page.getByRole("region", { name: "Presenter console" });
const player = (page: Page) => page.getByRole("group", { name: "Playback" });
const picker = (page: Page) => player(page).getByRole("combobox", { name: "Persona" });
const playerStatus = (page: Page) => page.getByTestId("player-status");
const playerDecision = (page: Page) => page.getByTestId("player-decision");

/**
 * Opens the Presenter console with Ctrl+. (it is not in the DOM until then). The host is a lazy chunk, so a
 * press before it has mounted is lost: press again until the panel shows, never when it already does.
 */
async function openConsole(page: Page): Promise<void> {
  await expect(async () => {
    if (!(await consolePanel(page).isVisible())) await page.keyboard.press("Control+.");
    await expect(consolePanel(page)).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 20_000 });
}

async function choosePersona(page: Page, id: string): Promise<void> {
  await openConsole(page);
  await expect(player(page)).toBeVisible();
  await picker(page).selectOption(id);
  await expect(player(page).getByRole("button", { name: "Play" })).toBeEnabled();
}

test.describe("playback control", () => {
  test("confirm_correction_mx reaches Qualified within 75 s of Play at the default pace", async ({ page }) => {
    await openStage(page);
    await expectNoPageScroll(page);
    await choosePersona(page, "confirm_correction_mx");
    const started = Date.now();
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(playerStatus(page)).toHaveText("Running");
    await expect(page.getByTestId("record-stage")).toContainText("Qualified", { timeout: 75_000 });
    const elapsed = Date.now() - started;
    test.info().annotations.push({ type: "qualified", description: `${(elapsed / 1000).toFixed(1)} s from Play to Qualified` });
    console.log(`confirm_correction_mx: Qualified ${(elapsed / 1000).toFixed(1)} s after Play`);
    expect(elapsed).toBeLessThanOrEqual(75_000);
    // The persona's own words were typed: the first one, and the corrected Zone at the confirm step.
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "Hola, quiero ser repartidor" })).toBeVisible();
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "no, mi zona es La Paz" })).toBeVisible();
    await expect(playerStatus(page)).toContainText("Stopped", { timeout: 30_000 });
    // With the control on the stage the record still fits and the page does not scroll.
    await expectNoPageScroll(page);
    const fit = await recordPane(page).evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
    expect(fit.scrollHeight).toBeLessThanOrEqual(fit.clientHeight);
  });

  test("no_license_mx reaches Knockout, sends its revisión and says why it stopped", async ({ page }) => {
    await openStage(page);
    await choosePersona(page, "no_license_mx");
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(playerStatus(page)).toHaveText("Stopped: Knockout", { timeout: 75_000 });
    // The interrupt after the Knockout: the Candidate's own word, as a bubble of theirs.
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "revisión" })).toBeVisible();
    await expect(recordPane(page).getByRole("list", { name: "Latest events" })).toContainText("Knockout", { timeout: 30_000 });
    await expect(playerDecision(page)).toHaveText(/revisión|interrupt at after_knockout/);
    await expectNoPageScroll(page);
  });

  test("a message typed by hand while paused is sent and shown, and the last decision does not move", async ({ page }) => {
    await openStage(page);
    await choosePersona(page, "confirm_correction_mx");
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(playerDecision(page)).toHaveText("sent the opening", { timeout: 30_000 });
    await player(page).getByRole("button", { name: "Pause" }).click();
    await expect(playerStatus(page)).toHaveText("Paused");
    const decision = await playerDecision(page).textContent();
    await composer(page).fill("hola, una pregunta");
    await sendButton(page).click();
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "hola, una pregunta" })).toBeVisible();
    // The Operator took over: nothing the player counts has moved, and it does not carry on by itself.
    await expect(playerDecision(page)).toHaveText(decision ?? "");
    await expect(playerStatus(page)).toHaveText("Paused");
    await expect(phonePane(page).getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });
    await expect(playerDecision(page)).toHaveText(decision ?? "");
  });

  test("lists a persona that moves the sim clock as unavailable, with its reason", async ({ page }) => {
    await openStage(page);
    await openConsole(page);
    await expect(player(page)).toBeVisible();
    const option = picker(page).locator('option[value="withdrawn_mx"]');
    await expect(option).toBeDisabled();
    await expect(option).toContainText("unavailable: moves the sim clock");
    await expect(picker(page).locator('option[value="confirm_correction_mx"]')).toBeEnabled();
  });

  test("has no accessibility violations with the control visible, idle and paused", async ({ page }) => {
    await openStage(page);
    await openConsole(page);
    await expect(player(page)).toBeVisible();
    await expectNoA11yViolations(page);
    await choosePersona(page, "confirm_correction_mx");
    await player(page).getByRole("button", { name: "Play" }).click();
    await expect(playerDecision(page)).toHaveText("sent the opening", { timeout: 30_000 });
    await player(page).getByRole("button", { name: "Pause" }).click();
    await expect(playerStatus(page)).toHaveText("Paused");
    await expectNoA11yViolations(page);
    await expectNoPageScroll(page);
  });

  test("is not linked from the Main navigation, for either role", async ({ page }) => {
    for (const [email, landing] of [
      [OPERATOR, "/overview"],
      [PUEBLA_RECRUITER, undefined],
    ] as const) {
      await logIn(page, email, landing ? { landing } : {});
      const main = page.getByRole("navigation", { name: "Main" });
      await expect(main).toBeVisible();
      await expect(main.locator('a[href*="/stage"]')).toHaveCount(0);
      await expect(main.getByRole("link", { name: /stage|demo|playback|persona/i })).toHaveCount(0);
      await expect(player(page)).toHaveCount(0);
      await page.context().clearCookies();
    }
  });

  test("an Operator's visit downloads the persona text and a Recruiter's does not", async ({ browser }) => {
    // Every script body is read, and a body that cannot be read or is empty fails the test: an unread
    // script must never count as clean.
    async function scripts(email: string): Promise<{ bodies: string[]; unread: string[] }> {
      const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
      const page = await context.newPage();
      const reads: Array<Promise<string>> = [];
      const unread: string[] = [];
      page.on("response", (response) => {
        if (!/\.js(\?|$)/.test(new URL(response.url()).pathname)) return;
        reads.push(
          response.text().then(
            (body) => {
              if (body === "") unread.push(`${response.url()}: empty body`);
              return body;
            },
            (reason: unknown) => {
              unread.push(`${response.url()}: ${String(reason)}`);
              return "";
            },
          ),
        );
      });
      await logIn(page, email, email === OPERATOR ? { landing: "/overview" } : {});
      await page.goto("/stage");
      if (email === OPERATOR) await page.waitForLoadState("networkidle");
      if (email === OPERATOR) await openConsole(page);
      if (email === OPERATOR) await expect(player(page)).toBeVisible();
      else await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const bodies = await Promise.all(reads);
      await context.close();
      return { bodies, unread };
    }
    const operator = await scripts(OPERATOR);
    expect(operator.unread).toEqual([]);
    expect(operator.bodies.some((body) => body.includes("no_license_mx"))).toBe(true);
    const recruiter = await scripts(PUEBLA_RECRUITER);
    expect(recruiter.bodies.length).toBeGreaterThan(0);
    expect(recruiter.unread).toEqual([]);
    expect(recruiter.bodies.filter((body) => body.includes("no_license_mx"))).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------
// The Presenter console (stage-console SC1, SC3, SC4): hidden by default, Prepare take, the takes list.
// ---------------------------------------------------------------------------------------------

const takesTable = (page: Page) => consolePanel(page).getByRole("table", { name: "Takes" });
const takeRows = (page: Page) => takesTable(page).locator("tbody tr");

test.describe("presenter console", () => {
  test("shows nothing of itself while closed, and closes again with Escape", async ({ page }) => {
    await openStage(page);
    await page.waitForLoadState("networkidle");
    await expect(consolePanel(page)).toHaveCount(0);
    await expect(page.getByText("Presenter console")).toHaveCount(0);
    await expect(player(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /new take|prepare take/i })).toHaveCount(0);
    await expect(page.getByRole("link")).toHaveCount(0);
    await openConsole(page);
    await expect(player(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(consolePanel(page)).toHaveCount(0);
    await expect(player(page)).toHaveCount(0);
    await expect(page.getByText("Presenter console")).toHaveCount(0);
  });

  test("does not scroll the page at 1920x1080, open or closed, and stays inside the window", async ({ page }) => {
    await openStage(page);
    await page.waitForLoadState("networkidle");
    await expectNoPageScroll(page);
    await openConsole(page);
    await expectNoPageScroll(page);
    const box = await consolePanel(page).boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? -1) + (box?.width ?? 0)).toBeLessThanOrEqual(1920);
    expect((box?.y ?? -1) + (box?.height ?? 0)).toBeLessThanOrEqual(1080);
    expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect(box?.y ?? -1).toBeGreaterThanOrEqual(0);
    await page.keyboard.press("Escape");
    await expect(consolePanel(page)).toHaveCount(0);
    await expectNoPageScroll(page);
  });

  test("has no accessibility violations with the console open and empty", async ({ page }) => {
    await openStage(page);
    await openConsole(page);
    await expect(takesTable(page)).toContainText("No takes yet. New take starts one.");
    await expect(consolePanel(page).getByRole("group", { name: "Clock controls" })).toBeVisible();
    await expectNoA11yViolations(page);
  });

  test("asks in a Dialog before a clock reset, which loads with the confirm; Cancel sends nothing and focus returns", async ({ page }) => {
    const resets: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().includes("/api/sim/clock/")) resets.push(request.url());
    });
    await openStage(page);
    await openConsole(page);
    const reset = consolePanel(page).getByRole("button", { name: "Reset clock" });
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await reset.click();
    const dialog = page.getByRole("dialog", { name: "Reset the clock?" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("moves the clock back to real time");
    await expectNoA11yViolations(page);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(reset).toBeFocused();
    expect(resets).toEqual([]);
  });

  test("Escape closes it and gives focus back to what had it", async ({ page }) => {
    await openStage(page);
    await page.waitForLoadState("networkidle");
    const start = phonePane(page).getByRole("button", { name: "Start conversation" });
    await start.focus();
    await expect(start).toBeFocused();
    await openConsole(page);
    expect(await consolePanel(page).evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(consolePanel(page)).toHaveCount(0);
    await expect(start).toBeFocused();
    // The same with the chord.
    await openConsole(page);
    await page.keyboard.press("Control+.");
    await expect(consolePanel(page)).toHaveCount(0);
    await expect(start).toBeFocused();
  });

  test("is not linked from the Main navigation for either role, and the chord does nothing off the stage", async ({ page }) => {
    for (const [email, landing] of [
      [OPERATOR, "/overview"],
      [PUEBLA_RECRUITER, undefined],
    ] as const) {
      await logIn(page, email, landing ? { landing } : {});
      const main = page.getByRole("navigation", { name: "Main" });
      await expect(main).toBeVisible();
      await expect(main.locator('a[href*="/stage"]')).toHaveCount(0);
      await expect(main.getByRole("link", { name: /stage|demo|console|presenter|take/i })).toHaveCount(0);
      await page.waitForLoadState("networkidle");
      await page.keyboard.press("Control+.");
      await expect(consolePanel(page)).toHaveCount(0);
      await expect(page.getByText("Presenter console")).toHaveCount(0);
      await page.context().clearCookies();
    }
  });

  test("Prepare take leaves a Knockout take, reopening it shows its history, and the list and the last take survive /overview", async ({ page }) => {
    await openStage(page);
    await openConsole(page);
    await expect(takesTable(page)).toContainText("No takes yet. New take starts one.");

    // Prepare take plays no_license_mx to its end, at the fastest pace, with its progress shown.
    await consolePanel(page).getByRole("button", { name: "Prepare take" }).click();
    await expect(consolePanel(page).getByRole("status").filter({ hasText: /^Preparing: \d+ of \d+$/ })).toBeVisible({ timeout: 30_000 });
    await expect(takeRows(page)).toHaveCount(1);
    await expect(takeRows(page).first()).toContainText("no_license_mx", { timeout: 15_000 });
    await expect(takeRows(page).first()).toContainText("knockout", { timeout: 120_000 });
    await expect(consolePanel(page).getByText(/^Preparing:/)).toHaveCount(0);
    await expect(playerStatus(page)).not.toHaveText("Running");
    const knockoutPhone = ((await takeRows(page).first().locator("td").nth(1).textContent()) ?? "").trim();
    expect(knockoutPhone).toMatch(/^\+52[0-9]{10}$/);
    await expectNoA11yViolations(page);

    // A second take, by hand: New take, then the start card, then a message. It is the newest in the list.
    await consolePanel(page).getByRole("button", { name: "New take" }).click();
    await expect(phonePane(page).getByTestId("start-phone")).toBeVisible();
    const handPhone = ((await phonePane(page).getByTestId("start-phone").textContent()) ?? "").trim();
    expect(handPhone).not.toBe(knockoutPhone);
    await phonePane(page).getByRole("button", { name: "Start conversation" }).click();
    await composer(page).fill("Hola, quiero trabajar de repartidor");
    await sendButton(page).click();
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "Hola, quiero trabajar de repartidor" })).toBeVisible();
    await expect(takeRows(page)).toHaveCount(2);
    await expect(takeRows(page).first()).toContainText(handPhone);
    await expect(takeRows(page).nth(1)).toContainText(knockoutPhone);

    // Reopening the Knockout take shows its history, with the revisión the persona sent, and its record.
    await expect(phonePane(page).getByTestId("phone-header").locator(".phone-status")).toHaveText("online", { timeout: 60_000 });
    await takeRows(page).nth(1).getByRole("button", { name: "Reopen" }).click();
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "revisión" })).toBeVisible({ timeout: 30_000 });
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "Hola, quiero trabajar de repartidor" })).toBeVisible();
    await expect(recordPane(page)).toContainText(knockoutPhone, { timeout: 30_000 });
    await expect(recordPane(page).getByRole("list", { name: "Latest events" })).toContainText("Knockout", { timeout: 30_000 });
    await expect(playerStatus(page)).not.toHaveText("Running");

    // With the panel closed the record is clickable again.
    await page.keyboard.press("Escape");
    await expect(consolePanel(page)).toHaveCount(0);
    await recordPane(page).getByRole("tab", { name: "Recruiter's view" }).click();
    await expect(recordPane(page).getByRole("tab", { name: "Recruiter's view" })).toHaveAttribute("aria-selected", "true");

    // /overview and back: the list is restored and the last take (the hand one) is on the phone again.
    await page.goto("/overview");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.goto("/stage");
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "Hola, quiero trabajar de repartidor" })).toBeVisible({ timeout: 30_000 });
    await expect(phonePane(page).locator('[data-side="right"]').filter({ hasText: "revisión" })).toHaveCount(0);
    await expect(phonePane(page).getByTestId("start-phone")).toHaveCount(0);
    // No networkidle here: the resumed conversation long-polls. The host that resumed it is mounted.
    await openConsole(page);
    await expect(takeRows(page)).toHaveCount(2);
    await expect(takeRows(page).first()).toContainText(handPhone);
    await expect(takeRows(page).nth(1)).toContainText("knockout", { timeout: 30_000 });
    await expectNoPageScroll(page);
    await expectNoA11yViolations(page);
  });
});
