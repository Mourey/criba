// The agent trace in the Operator panel (operator console, agent-trace-web): the "Agent runs" section
// compared with the tool-calls response, a Knockout call, a Recruiter who never asks for the trace, and
// axe in both themes. Stub model on the sim stack: nothing is hard-coded about how many calls a run holds.
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { expectNoA11yViolations } from "./a11y.ts";
import { apiAs, applicationFor, Candidate, candidateName, logIn, OPERATOR, phone, PUEBLA_RECRUITER, requireStack, screeningAnswers } from "./support.ts";

requireStack();

interface Call {
  tool: string;
  outcome: "ok" | "invalid" | "knockout" | "error";
  error: string | null;
  args: Record<string, unknown>;
}

interface Run {
  run_id: string | null;
  reply_message_ids: number[];
  calls: Call[];
}

interface Trace {
  runs: Run[];
}

const OPENING = "Hola, quiero trabajar de repartidor";
const OUTCOME_TEXT = { ok: "OK", invalid: "Invalid", knockout: "Knockout", error: "Error" } as const;

const tracePath = (id: string) => `/api/applications/${id}/tool-calls`;

/** The trace once a linked run with a reply exists and two reads in a row, 1.5 s apart, are the same. */
async function settledTrace(api: APIRequestContext, id: string): Promise<Trace> {
  let last = "";
  let same = 0;
  let trace: Trace = { runs: [] };
  await expect
    .poll(
      async () => {
        const response = await api.get(tracePath(id));
        expect(response.status(), await response.text()).toBe(200);
        trace = (await response.json()) as Trace;
        const text = JSON.stringify(trace);
        same = text === last ? same + 1 : 0;
        last = text;
        return same >= 1 && trace.runs.some((r) => r.run_id !== null && r.reply_message_ids.length > 0);
      },
      { timeout: 120_000, intervals: [1_500], message: `a settled trace with a linked run for ${id}` },
    )
    .toBe(true);
  return trace;
}

const sectionOf = (page: Page) => page.getByRole("region", { name: "Operator panel" }).getByRole("region", { name: "Agent runs" });
const runItems = (section: Locator) => section.getByRole("list", { name: "Runs with their tool calls", exact: true }).locator(":scope > li");
const callLines = (scope: Locator) => scope.getByRole("list", { name: "Tool calls", exact: true }).getByRole("listitem");

async function openAsOperator(page: Page, id: string): Promise<Locator> {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await logIn(page, OPERATOR);
  await page.goto(`/applications/${id}`);
  await expect(page.getByRole("region", { name: "Operator panel" })).toBeVisible();
  const section = sectionOf(page);
  await expect(section).toBeVisible();
  await expect(runItems(section).first()).toBeVisible();
  return section;
}

test.describe("The agent trace in the Operator panel", () => {
  let qualified = "";
  let knockout = "";
  let qualifiedTrace: Trace = { runs: [] };
  let knockoutTrace: Trace = { runs: [] };

  test.beforeAll(async () => {
    test.setTimeout(15 * 60_000);
    const api = await apiAs(OPERATOR);

    // A: Qualified in Puebla, so the Puebla Recruiter may open it.
    const a = await Candidate.start("MX");
    await a.say(OPENING);
    for (const answer of screeningAnswers()) await a.say(answer);
    qualified = (await applicationFor(api, a.phone, "qualified")).id;
    await a.close();

    // B: no driver's license, a Knockout (persona no_license_mx).
    const b = await Candidate.start("MX", phone("MX"));
    await b.say(OPENING);
    await b.say(candidateName());
    await b.say("Centro, Puebla");
    await b.say("No, todavía no tengo licencia de manejo");
    await expect
      .poll(async () => {
        const response = await api.get("/api/applications", { params: { q: b.phone } });
        const items = ((await response.json()).items ?? []) as { id: string; outcome: string | null }[];
        knockout = items[0]?.id ?? "";
        return items[0]?.outcome ?? null;
      }, { timeout: 60_000, message: "the Application ends as a Knockout" })
      .toBe("knockout");
    await b.close();

    qualifiedTrace = await settledTrace(api, qualified);
    knockoutTrace = await settledTrace(api, knockout);
    await api.dispose();
  });

  test("shows one item per run and one line per call, as the response gives them, and a jump to the reply", async ({ page }) => {
    const section = await openAsOperator(page, qualified);
    const runs = runItems(section);
    await expect(runs).toHaveCount(qualifiedTrace.runs.length);
    for (const [i, run] of qualifiedTrace.runs.entries()) {
      const item = runs.nth(i);
      await expect(item.getByRole("heading").first()).toHaveText(run.run_id === null ? "Run not recorded" : `Run ${run.run_id}`);
      const lines = callLines(item);
      await expect(lines).toHaveCount(run.calls.length);
      for (const [j, call] of run.calls.entries()) {
        await expect(lines.nth(j)).toContainText(call.tool);
        await expect(lines.nth(j).locator(".ui-tag")).toHaveText(OUTCOME_TEXT[call.outcome]);
      }
    }
    const total = qualifiedTrace.runs.reduce((sum, r) => sum + r.calls.length, 0);
    expect(total).toBeGreaterThan(0);
    await expect(callLines(section)).toHaveCount(total);

    // A stored null (the name answer keeps no value) reads "(not stored)", once per null in the response.
    const nulls = qualifiedTrace.runs.flatMap((r) => r.calls.flatMap((c) => Object.values(c.args))).filter((v) => v === null).length;
    expect(nulls).toBeGreaterThanOrEqual(1);
    await expect(section.getByText("(not stored)")).toHaveCount(nulls);

    // The first enabled jump marks one bubble in the Transcript: the first reply of the first run that has one.
    const firstReply = qualifiedTrace.runs.find((r) => r.run_id !== null && r.reply_message_ids.length > 0)!.reply_message_ids[0];
    const log = page.getByRole("log", { name: "Transcript" });
    await expect(log.locator("[data-selected]")).toHaveCount(0);
    await section.locator("button:not([disabled])", { hasText: "Show reply in Transcript" }).first().click();
    await expect(log.locator("[data-selected]")).toHaveCount(1);
    await expect(log.locator("[data-selected]")).toHaveAttribute("data-message-id", String(firstReply));
    await expect(log.locator("[data-selected]")).toBeInViewport();

    // Not a word the Candidate typed (the opening, the name) reaches the panel.
    const text = (await page.getByRole("region", { name: "Operator panel" }).innerText()).toLowerCase();
    for (const word of `${OPENING} ${candidateName()}`.split(/[\s,]+/).filter((w) => w.length >= 4)) {
      expect(text, `the panel must not repeat "${word}"`).not.toContain(word.toLowerCase());
    }
  });

  test("shows the Knockout call of the license answer with the outcome text Knockout", async ({ page }) => {
    const calls = knockoutTrace.runs.flatMap((r) => r.calls);
    const call = calls.find((c) => c.outcome === "knockout");
    expect(call, "the response holds a knockout call").toBeDefined();
    expect(call!.tool).toBe("record_answer");
    expect(call!.args.field).toBe("driver_license");

    const section = await openAsOperator(page, knockout);
    const line = callLines(section).filter({ hasText: "record_answer" }).filter({ hasText: "driver_license" }).filter({ has: page.locator(".ui-tag", { hasText: /^Knockout$/ }) });
    await expect(line.first()).toBeVisible();
  });

  test("keeps the trace from a Recruiter: no panel, no section, no request, and a 403 for a direct read", async ({ page }) => {
    const paths: string[] = [];
    page.on("request", (request) => paths.push(new URL(request.url()).pathname));
    await page.setViewportSize({ width: 1920, height: 1080 });
    await logIn(page, PUEBLA_RECRUITER);
    await page.goto(`/applications/${qualified}`);
    await expect(page.getByRole("log", { name: "Transcript" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Operator panel" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Agent runs" })).toHaveCount(0);
    await page.waitForLoadState("networkidle");
    expect(paths.filter((p) => p.endsWith("/tool-calls"))).toEqual([]);
    expect(paths.some((p) => p.includes(`/api/applications/${qualified}`))).toBe(true);

    const recruiter = await apiAs(PUEBLA_RECRUITER);
    const direct = await recruiter.get(tracePath(qualified));
    expect(direct.status(), await direct.text()).toBe(403);
    await recruiter.dispose();
  });

  test("has no axe violations in either theme with the panel open and the section shown", async ({ page }) => {
    const section = await openAsOperator(page, qualified);
    await expect(callLines(section).first()).toBeVisible();
    await expectNoA11yViolations(page);
  });
});
