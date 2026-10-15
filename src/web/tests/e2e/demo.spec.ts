// The presentation's demo as an acceptance test, in the browser (spec 004 task 12, ADR 0028).
// Against a sim stack (stub model, no spend):
//   1. a Knockout Candidate in the test chat page;
//   2. a Qualified Candidate on the web chat API;
//   3. the Puebla Recruiter Claims it in the tracker;
//   4. takes over, writes to the Candidate and hands back;
//   5. Approves it from the review queue;
//   6. the Candidate taps an offered slot, the Interview is booked and the .ics downloads;
//   7. the Operator's economics dashboard shows these runs.
// The API half is src/tests/e2e/test_demo.py.
import { expect, request as newRequest, test, type APIRequestContext } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  apiAs,
  applicationFor,
  BASE_URL,
  Candidate,
  candidateName,
  logIn,
  OPERATOR,
  phone,
  PUEBLA_RECRUITER,
  requireStack,
  screeningAnswers,
} from "./support.ts";

requireStack();

interface ChatMessage {
  id: number;
  sender: string;
  text: string;
  interactive: { options: { id: string; label: string }[] } | null;
  attachments: { type: string; url: string; filename: string }[];
}

interface ChatPage {
  cursor: number;
  run_finished?: boolean;
  messages: ChatMessage[];
}

/** Messages after `after` once the agent's turn is over (the web chat's run_finished). */
async function readTurn(api: APIRequestContext, conversationId: string, after: number): Promise<ChatMessage[]> {
  const messages: ChatMessage[] = [];
  const deadline = Date.now() + 60_000;
  let cursor = after;
  while (Date.now() < deadline) {
    const response = await api.get(`/api/chat/conversations/${conversationId}/messages`, {
      params: { after: cursor, wait: 5 },
      timeout: 20_000,
    });
    const page = (await response.json()) as ChatPage;
    messages.push(...page.messages);
    cursor = page.cursor;
    if (messages.length > 0 && page.run_finished) return messages;
  }
  throw new Error(`no end of the agent's turn after message ${after}`);
}

async function lastCursor(api: APIRequestContext, conversationId: string): Promise<number> {
  const response = await api.get(`/api/chat/conversations/${conversationId}/messages`, { params: { after: 0 } });
  return ((await response.json()) as ChatPage).cursor;
}

/** Move the sim clock out of quiet hours (22:00 to 09:00) in Mexico City, where the demo's
 * Candidates live, so the Approval's system turn runs at once instead of being held. */
async function daytimeInMexico(api: APIRequestContext): Promise<void> {
  await api.post("/api/sim/clock/reset");
  const now = new Date(((await (await api.get("/api/sim/clock")).json()) as { now: string }).now);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Mexico_City",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  }).formatToParts(now);
  const hour = Number(parts.find((p) => p.type === "hour")?.value);
  const minute = Number(parts.find((p) => p.type === "minute")?.value);
  if (hour >= 10 && hour < 19) return;
  const seconds = ((10 - hour + 24) % 24) * 3600 - minute * 60;
  const moved = await api.post("/api/sim/clock/advance", { data: { seconds } });
  expect(moved.status(), await moved.text()).toBe(200);
}

test("demo: Knockout, Qualified, Claim, Takeover, Approval, booked Interview, economics", async ({ page, browser }) => {
  const operator = await apiAs(OPERATOR);
  await daytimeInMexico(operator);

  // 1. A Knockout Candidate in the test chat page: no driver's license.
  const koNumber = phone("MX");
  await page.goto("/test-chat");
  await page.getByLabel("Country").selectOption("MX");
  await page.getByLabel("Language").selectOption("es-MX");
  await page.getByLabel(/^Phone/).fill(koNumber);
  await page.getByRole("button", { name: "Start conversation" }).click();
  const log = page.getByRole("log", { name: "Messages" });
  const agentBubbles = log.locator('[data-sender="agent"]');
  const composer = page.getByLabel("Message", { exact: true });
  const say = async (text: string, replies: number) => {
    await composer.fill(text);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(agentBubbles).toHaveCount(replies, { timeout: 60_000 });
  };
  await say("Hola, quiero trabajar de repartidor", 1);
  await say(candidateName(), 2);
  await say("Centro, Puebla", 3);
  await log.getByRole("button", { name: "No", exact: true }).click();
  await expect
    .poll(async () => {
      const response = await operator.get("/api/applications", { params: { q: koNumber } });
      const items = (await response.json()).items as { outcome: string | null }[];
      return items[0]?.outcome ?? null;
    }, { timeout: 60_000 })
    .toBe("knockout");
  await expect(page.getByText("Typing...")).toHaveCount(0, { timeout: 30_000 });

  // 2. A Qualified Candidate in Puebla, on the web chat API.
  const name = candidateName();
  const candidate = await Candidate.start("MX");
  await candidate.say("Hola, quiero trabajar de repartidor");
  for (const answer of screeningAnswers()) await candidate.say(answer);
  const app = await applicationFor(operator, candidate.phone, "qualified");

  // 3. The Puebla Recruiter finds it in the tracker and Claims it.
  await logIn(page, PUEBLA_RECRUITER);
  await page.getByRole("searchbox", { name: "Search Candidate" }).fill(candidate.phone);
  const row = page.getByRole("row").filter({ hasText: candidate.phone });
  await expect(row).toContainText("Qualified");
  await row.getByRole("link", { name }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${app.id}$`));
  const actions = page.getByRole("region", { name: "Actions" });
  await actions.getByRole("button", { name: "Claim", exact: true }).click();
  await expect(actions).toContainText(/Recruiter .* since/);

  // 4. Takeover: a Team message reaches the Candidate; then hand back to the Screening Agent.
  const takeover = page.getByRole("region", { name: "Takeover" });
  await takeover.getByRole("button", { name: "Start Takeover" }).click();
  await expect(takeover).toContainText("You hold this chat.");
  const text = `Hola ${name.split(" ")[0]}, soy del equipo de Grupo Sazón. Te confirmo la entrevista enseguida.`;
  await takeover.getByLabel("Message to the Candidate").fill(text);
  await takeover.getByRole("button", { name: "Send" }).click();
  await expect.poll(async () => (await candidate.inbox()).includes(text)).toBe(true);
  await takeover.getByLabel(/Summary for the Screening Agent/).fill("Saludé al Candidato; espera la Approval.");
  await takeover.getByRole("button", { name: "Hand back to the Screening Agent" }).click();
  await expect(takeover).toContainText("The Screening Agent holds this chat.");
  await expect(page.getByRole("region", { name: "Timeline" })).toContainText("Takeover handed back");

  // 5. Approval from the review queue.
  const chat = await newRequest.newContext({ baseURL: BASE_URL });
  const before = await lastCursor(chat, candidate.conversationId);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Review queue" }).click();
  const card = page.getByRole("article").filter({ has: page.locator(`a[href="/applications/${app.id}"]`) });
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card).toHaveCount(0);

  // 6. The agent offers slots; the Candidate taps one; the confirmation carries the .ics.
  const offered = await readTurn(chat, candidate.conversationId, before);
  const offer = offered.find((m) => m.interactive);
  expect(offer, JSON.stringify(offered)).toBeTruthy();
  const slot = offer!.interactive!.options.find((o) => o.id.startsWith("so_"));
  expect(slot).toBeTruthy();
  const tapped = await chat.post(`/api/chat/conversations/${candidate.conversationId}/replies`, {
    data: { message_id: crypto.randomUUID(), in_reply_to: offer!.id, option_id: slot!.id },
  });
  expect(tapped.status(), await tapped.text()).toBe(202);
  const booked = await readTurn(chat, candidate.conversationId, offer!.id);
  const invite = booked.flatMap((m) => m.attachments ?? []).find((a) => a.type === "ics");
  expect(invite, JSON.stringify(booked)).toBeTruthy();
  const ics = await chat.get(invite!.url);
  expect(ics.status()).toBe(200);
  expect(ics.headers()["content-type"]).toContain("text/calendar");
  expect(await ics.text()).toContain("BEGIN:VEVENT");

  await page.goto(`/applications/${app.id}`);
  await expect(page.getByRole("region", { name: "Timeline" })).toContainText("Interview booked");
  await expect(page.getByRole("heading", { name: "Interview" })).toBeVisible();

  // 7. The Operator's economics dashboard shows these runs (the stub model's).
  const operatorPage = await browser.newPage();
  await logIn(operatorPage, OPERATOR);
  await operatorPage.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Economics" }).click();
  await expect(operatorPage.getByRole("heading", { level: 1, name: "Economics" })).toBeVisible();
  await operatorPage.getByRole("combobox", { name: "Environment" }).selectOption("sim");
  await expect(operatorPage.getByText(/\d+ conversations/)).toBeVisible();
  const mix = operatorPage.getByRole("region", { name: "Model mix" });
  await expect(mix.getByRole("row").filter({ hasText: "stub-model" }).first()).toBeVisible();
  await expect(operatorPage.getByRole("alert")).toHaveCount(0);
  await operatorPage.close();

  await chat.dispose();
  await operator.dispose();
  await candidate.close();
});

// The five-minute demo storyline: Stage, Recruiter,
// Stage, Operator, with no terminal and no shell. The stage is the Operator's and Claim, Takeover and
// Approve are the Puebla Recruiter's, so there are two browser contexts, each with its own cookie jar as
// two browser profiles have. On the stub: this proves the plumbing and the route order, not the wording or
// the timing on the real model (that takes a manual run on the real model).
test("storyline: Stage, Recruiter, Stage, Operator", async ({ browser }) => {
  test.setTimeout(480_000);
  const clock = await apiAs(OPERATOR);
  const routes: string[] = [];
  const at = (who: string, page: Page, id?: string) => {
    const path = new URL(page.url()).pathname;
    routes.push(`${who}: ${id ? path.replace(id, ":id") : path}`);
  };

  const operatorContext = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const recruiterContext = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const stage = await operatorContext.newPage();
  const recruiter = await recruiterContext.newPage();

  const phonePane = stage.getByRole("region", { name: "Phone" });
  const recordPane = stage.getByRole("region", { name: "Record" });
  const console_ = stage.getByRole("region", { name: "Presenter console" });
  const takeRows = console_.getByRole("table", { name: "Takes" }).locator("tbody tr");
  /** Ctrl+. until the panel shows: the console host is a lazy chunk, so a press before it mounts is lost. */
  const openConsole = async () => {
    await expect(async () => {
      if (!(await console_.isVisible())) await stage.keyboard.press("Control+.");
      await expect(console_).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 20_000 });
  };

  // 1. Stage. Prepare take plays the Knockout Candidate; then a New take with confirm_correction_mx plays to Qualified.
  await logIn(stage, OPERATOR, { landing: "/overview" });
  await stage.goto("/stage");
  await expect(phonePane).toBeVisible();
  at("operator", stage);
  await openConsole();
  await console_.getByRole("button", { name: "Prepare take" }).click();
  await expect(takeRows.first()).toContainText("knockout", { timeout: 120_000 });
  await expect(takeRows.first()).toContainText("no_license_mx");

  await console_.getByRole("combobox", { name: "Persona" }).selectOption("confirm_correction_mx");
  await console_.getByLabel("Characters per second").fill("0");
  await console_.getByLabel("Pause before each message (seconds)").fill("0");
  await console_.getByRole("button", { name: "New take" }).click();
  const takePhone = ((await phonePane.getByTestId("start-phone").textContent()) ?? "").trim();
  expect(takePhone).toMatch(/^\+52[0-9]{10}$/);
  await console_.getByRole("button", { name: "Play" }).click();
  await expect(stage.getByTestId("record-stage")).toContainText("Qualified", { timeout: 150_000 });
  await expect(takeRows).toHaveCount(2);
  await expect(takeRows.first()).toContainText(takePhone);

  // The stage moves aside; the Recruiter's window takes over.
  await stage.goto("/overview");

  // 2. Recruiter. The clock is in the window first, so the Approval's turn runs at once. Today, the Application,
  // Claim, Takeover with one message, hand back, Approve.
  await daytimeInMexico(clock);
  const recruiterApi = await apiAs(PUEBLA_RECRUITER);
  const app = await applicationFor(recruiterApi, takePhone, "qualified");
  await logIn(recruiter, PUEBLA_RECRUITER);
  await recruiter.goto("/today");
  await expect(recruiter.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  at("recruiter", recruiter);
  await recruiter.locator(`a[href="/applications/${app.id}"]`).first().click();
  await expect(recruiter).toHaveURL(new RegExp(`/applications/${app.id}$`));
  at("recruiter", recruiter, app.id);
  const actions = recruiter.getByRole("region", { name: "Actions" });
  await actions.getByRole("button", { name: "Claim", exact: true }).click();
  await expect(actions).toContainText(/Recruiter .* since/);
  const takeover = recruiter.getByRole("region", { name: "Takeover" });
  await takeover.getByRole("button", { name: "Start Takeover" }).click();
  await expect(takeover).toContainText("You hold this chat.");
  const team = `Hola ${candidateName().split(" ")[0]}, soy del equipo de Grupo Sazón. Te confirmo la entrevista enseguida.`;
  await takeover.getByLabel("Message to the Candidate").fill(team);
  await takeover.getByRole("button", { name: "Send" }).click();
  await expect(takeover.getByLabel("Message to the Candidate")).toHaveValue("");
  await takeover.getByLabel(/Summary for the Screening Agent/).fill("Saludé al Candidato; espera la Approval.");
  await takeover.getByRole("button", { name: "Hand back to the Screening Agent" }).click();
  await expect(takeover).toContainText("The Screening Agent holds this chat.");
  await actions.getByRole("button", { name: "Approve" }).click();
  await expect(actions.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // 3. Stage again: the last take resumes on the phone with the Team's message, the agent offers slots, the
  // Candidate taps one, the .ics card appears and the record shows the Interview.
  await stage.goto("/stage");
  await expect(phonePane.getByTestId("start-phone")).toHaveCount(0);
  at("operator", stage);
  await expect(phonePane.getByText(team)).toBeVisible({ timeout: 30_000 });
  const live = phonePane.locator(".phone-reply:not(:disabled), .phone-list:not(:disabled)").last();
  await expect(live).toBeVisible({ timeout: 90_000 });
  if ((await live.getAttribute("class"))?.includes("phone-list")) {
    await live.click();
    await phonePane.getByRole("dialog", { name: "Options" }).getByRole("button").first().click();
  } else {
    await live.click();
  }
  await expect(phonePane.getByText("Interview invite")).toBeVisible({ timeout: 90_000 });
  await expect(stage.getByTestId("record-stage")).toContainText(/Interview/, { timeout: 60_000 });
  await expect(recordPane).not.toContainText("No Interview yet");
  await expect(recordPane.getByText("Interview", { exact: true }).first()).toBeVisible();

  // 4. Operator. /overview, then Economics through the Main navigation: the cost per conversation.
  await stage.goto("/overview");
  await expect(stage.getByRole("navigation", { name: "Main" })).toBeVisible();
  at("operator", stage);
  await stage.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Economics" }).click();
  await expect(stage.getByRole("heading", { level: 1, name: "Economics" })).toBeVisible();
  await expect(stage.getByText("Cost per conversation")).toBeVisible();
  at("operator", stage);

  expect(routes).toEqual([
    "operator: /stage",
    "recruiter: /today",
    "recruiter: /applications/:id",
    "operator: /stage",
    "operator: /overview",
    "operator: /economics",
  ]);

  await recruiterApi.dispose();
  await clock.dispose();
  await operatorContext.close();
  await recruiterContext.close();
});
