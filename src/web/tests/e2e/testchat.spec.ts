// The test chat page is a real web chat client: a short conversation with the stub model, after
// which the Application appears in the Applications list.
import { expect, test } from "@playwright/test";
import { candidateName, logIn, OPERATOR, phone, requireStack } from "./support.ts";

requireStack();

test("the test chat runs a conversation and the Application appears in the list", async ({ page }) => {
  const number = phone("MX");
  const name = candidateName();

  await page.goto("/test-chat");
  await page.getByLabel("Country").selectOption("MX");
  await page.getByLabel("Language").selectOption("es-MX");
  await page.getByLabel(/^Phone/).fill(number);
  await page.getByRole("button", { name: "Start conversation" }).click();

  // The channel names the agent and the conversation's language.
  await expect(page.getByTestId("chat-meta")).toContainText("Agent: Sazón Empleo");
  await expect(page.getByTestId("chat-meta")).toContainText("Language: Spanish (Mexico)");

  const log = page.getByRole("log", { name: "Messages" });
  const composer = page.getByLabel("Message", { exact: true });
  const agentBubbles = log.locator('[data-sender="agent"]');
  const say = async (text: string, replies: number) => {
    await composer.fill(text);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(log.locator(".bubble.out").filter({ hasText: text })).toBeVisible();
    await expect(agentBubbles).toHaveCount(replies, { timeout: 60_000 });
  };

  await say("Hola, quiero trabajar de repartidor", 1);
  await say(name, 2);
  await say("Centro, Puebla", 3);
  // The driver's license question comes with buttons (ADR 0037); tap one.
  await log.getByRole("button", { name: "Sí, tengo licencia" }).click();
  await expect(agentBubbles).toHaveCount(4, { timeout: 60_000 });
  // The next question (availability) brings its own buttons. Its wording is the stub model's,
  // which reads the tap's option id ("yes") as English, so only the options are checked.
  await expect(agentBubbles.last().getByRole("button", { name: "Tiempo completo" })).toBeEnabled();
  await expect(log.getByRole("button", { name: "Sí, tengo licencia" })).toBeDisabled();
  await expect(page.getByText("Typing...")).toHaveCount(0);

  // The Application is in the list, with the Candidate's name and where Screening stands.
  await logIn(page, OPERATOR);
  await page.getByRole("searchbox", { name: "Search Candidate" }).fill(number);
  const row = page.getByRole("row").filter({ hasText: number });
  await expect(row).toHaveCount(1);
  await expect(row.getByRole("link", { name })).toBeVisible();
  await expect(row).toContainText("Availability");
  await expect(row).toContainText("Sazón Puebla Centro");
});
