// The Operator conversation list and its read-only view (operator console chat-history-web, CH4, CH5,
// M4). Conversations are started on the public /test-chat page and through the web chat API, then read
// as an Operator. Newest first is by creation time to the second, so starts are a second apart, and the
// stack is restarted (down -v) before this file runs: an earlier run's rows, or a sim clock moved by
// demo.spec.ts or stage.spec.ts, could otherwise sort above these.
import { expect, request as newRequest, test, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { BASE_URL, Candidate, candidateName, logIn, OPERATOR, phone, PUEBLA_RECRUITER, requireStack } from "./support.ts";

requireStack();

const STORAGE_KEY = "criba.testChat.conversation";
const OPENING = "Hola, quiero trabajar de repartidor";
const PERSONA = "confirm_correction_mx";

/** Creation times are stored to the second. */
const SECOND_APART = 1_100;

/** Start a conversation on the public /test-chat page; returns its id from the page's own storage. */
async function startTestChat(page: Page, number: string): Promise<string> {
  await page.goto("/test-chat");
  await page.getByLabel("Country").selectOption("MX");
  await page.getByLabel("Language").selectOption("es-MX");
  await page.getByLabel(/^Phone/).fill(number);
  await page.getByRole("button", { name: "Start conversation" }).click();
  await expect(page.getByLabel("Message", { exact: true })).toBeEnabled();
  const id = await page.evaluate((key) => window.sessionStorage.getItem(key), STORAGE_KEY);
  expect(id).toMatch(/^wc_/);
  return id as string;
}

/** Start a Demo stage conversation the way the persona player does: client demo with the persona's id. */
async function startDemo(number: string): Promise<string> {
  const api = await newRequest.newContext({ baseURL: BASE_URL });
  try {
    const response = await api.post("/api/chat/conversations", {
      data: { country: "MX", locale: "es-MX", phone: number, client: "demo", persona_id: PERSONA },
    });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()).conversation_id as string;
  } finally {
    await api.dispose();
  }
}

const bodyRows = (page: Page) => page.getByRole("table").locator("tbody tr");

test("M4: two Test chat conversations and a Demo stage one are listed newest first, and each opens", async ({ page, context }) => {
  const shared = phone("MX");

  // A: on the public page, with one message and the agent's reply.
  const idA = await startTestChat(page, shared);
  const log = page.getByRole("log", { name: "Messages" });
  await page.getByLabel("Message", { exact: true }).fill(OPENING);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(log.locator('[data-sender="agent"]')).toHaveCount(1, { timeout: 60_000 });

  await page.waitForTimeout(SECOND_APART);
  const idDemo = await startDemo(phone("MX"));
  await page.waitForTimeout(SECOND_APART);

  // B: a second tab (its own sessionStorage), the same phone.
  const second = await context.newPage();
  const idB = await startTestChat(second, shared);
  await second.close();
  expect(new Set([idA, idDemo, idB]).size).toBe(3);

  await logIn(page, OPERATOR);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Test chat" }).click();
  await expect(page).toHaveURL(/\/conversations$/);
  await expect(page.getByRole("heading", { level: 1, name: "Test chat" })).toBeVisible();

  const rows = bodyRows(page);
  await expect(rows.nth(2)).toBeVisible();
  const expected = [
    { id: idB, name: shared, client: "Test chat" },
    { id: idDemo, name: candidateName(), client: "Demo stage" },
    { id: idA, name: shared, client: "Test chat" },
  ];
  for (const [i, row] of expected.entries()) {
    const link = rows.nth(i).getByRole("link", { name: row.name });
    await expect(link).toHaveAttribute("href", `/conversations/${row.id}`);
    await expect(rows.nth(i)).toContainText(row.client);
  }

  for (const row of expected) {
    await page.goto("/conversations");
    await page.getByRole("link", { name: row.name }).and(page.locator(`[href="/conversations/${row.id}"]`)).click();
    await expect(page).toHaveURL(new RegExp(`/conversations/${row.id}$`));
    await expect(page.getByRole("heading", { level: 1, name: row.name })).toBeVisible();
    await expect(page.getByRole("main").getByText(row.client, { exact: true })).toBeVisible();
  }
});

test("CH5: the view is read-only, notes the shared phone, and Resume continues on /test-chat", async ({ page }) => {
  const shared = phone("MX");
  const a = await Candidate.start("MX", shared);
  await a.say(OPENING);
  await a.close();
  await page.waitForTimeout(SECOND_APART);
  const b = await Candidate.start("MX", shared);
  await b.close();

  // A fresh context: nothing in sessionStorage yet.
  await logIn(page, OPERATOR);
  expect(await page.evaluate((key) => window.sessionStorage.getItem(key), STORAGE_KEY)).toBeNull();
  await page.goto(`/conversations/${b.conversationId}`);
  await expect(page.getByRole("heading", { level: 1, name: shared })).toBeVisible();
  const transcript = page.getByRole("log");
  await expect(transcript.getByText(OPENING)).toBeVisible();
  await expect(transcript.locator('[data-sender="agent"]')).toHaveCount(1);
  await expect(page.getByText("Messages from every web-chat conversation with this phone.")).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);

  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page).toHaveURL(/\/test-chat$/);
  expect(await page.evaluate((key) => window.sessionStorage.getItem(key), STORAGE_KEY)).toBe(b.conversationId);

  const log = page.getByRole("log", { name: "Messages" });
  await expect(log.locator(".bubble.out").filter({ hasText: OPENING })).toBeVisible();
  const agent = log.locator('[data-sender="agent"]');
  await expect(agent).toHaveCount(1);
  await page.getByLabel("Message", { exact: true }).fill(candidateName());
  await page.getByRole("button", { name: "Send" }).click();
  await expect(log.locator(".bubble.out").filter({ hasText: candidateName() })).toBeVisible();
  await expect(agent).toHaveCount(2, { timeout: 60_000 });
});

test("access: a Recruiter gets the not-found page and a Test chat link to /test-chat; an Operator's goes to the list", async ({ browser }) => {
  const recruiter = await browser.newPage();
  await logIn(recruiter, PUEBLA_RECRUITER);
  const recruiterLink = recruiter.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Test chat" });
  await expect(recruiterLink).toHaveAttribute("href", "/test-chat");
  await recruiter.goto("/conversations");
  await expect(recruiter.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(recruiter.getByRole("table")).toHaveCount(0);
  await recruiter.context().close();

  const operator = await browser.newPage();
  await logIn(operator, OPERATOR);
  const operatorLink = operator.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Test chat" });
  await expect(operatorLink).toHaveAttribute("href", "/conversations");
  await operator.context().close();
});

test("axe: the list and the view have no violations in either theme", async ({ page }) => {
  const candidate = await Candidate.start("MX");
  await candidate.say(OPENING);
  await candidate.close();

  await logIn(page, OPERATOR);
  await page.goto("/conversations");
  await expect(bodyRows(page).first()).toBeVisible();
  await expectNoA11yViolations(page);

  await page.goto(`/conversations/${candidate.conversationId}`);
  await expect(page.getByRole("heading", { level: 1, name: candidate.phone })).toBeVisible();
  await expect(page.getByRole("log")).toBeVisible();
  await expectNoA11yViolations(page);
});
