// Shared helpers for the end-to-end specs. They drive a real sim stack (stub model, no real model
// calls): Candidate activity goes through the public web chat API, people use the browser.
import { readFileSync } from "node:fs";
import { expect, request as newRequest, test, type APIRequestContext, type Page } from "@playwright/test";

export const BASE_URL = process.env.CRIBA_E2E_BASE_URL ?? "";

function seedPassword(): string {
  const path = process.env.CRIBA_E2E_SEED_PASSWORD_FILE;
  if (path) {
    try {
      return readFileSync(path, "utf8").trim();
    } catch {
      return "";
    }
  }
  return process.env.CRIBA_E2E_SEED_PASSWORD ?? "";
}

export const PASSWORD = seedPassword();

export const OPERATOR = "operator@criba.example";
export const PUEBLA_RECRUITER = "recruiter.puebla@sazon.example";

/** Skip the whole file unless a stack and its seed password are given, and fail it unless that
 * stack is a sim stack: only a sim stack mounts GET /api/sim/clock (stub model, no real model). */
export function requireStack(): void {
  test.skip(!BASE_URL, "set CRIBA_E2E_BASE_URL to a sim stack's web URL");
  test.skip(!PASSWORD, "set CRIBA_E2E_SEED_PASSWORD_FILE (the stack's seed password)");
  test.beforeAll(async () => {
    const api = await newRequest.newContext({ baseURL: BASE_URL });
    try {
      const response = await api.get("/api/sim/clock");
      expect(response.status(), `${BASE_URL} is not a sim stack: GET /api/sim/clock answered ${response.status()}`).toBe(200);
    } finally {
      await api.dispose();
    }
  });
}

/** The stub model cannot read words: it looks up the seed personas' own messages (spec 006b,
 * `src/tests/sim/stub_llm/interpret.py`). Any other text interprets to null, a miss. So the specs
 * speak as the persona `confirm_correction_mx` does (Puebla, Qualified), and a name is the one the
 * persona gives. Candidates differ by phone number, which is random. */
export function candidateName(): string {
  return "Diego Martínez";
}

export function phone(country: "ES" | "MX"): string {
  const digits = String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  return country === "ES" ? `+346${digits}` : `+5255${digits}`;
}

interface ChatPage {
  cursor: number;
  run_finished?: boolean;
  messages: { id: number; direction: string; sender: string; text: string }[];
}

/** A Candidate on the web chat API. */
export class Candidate {
  private cursor = 0;
  readonly transcript: ChatPage["messages"] = [];

  private constructor(
    private readonly api: APIRequestContext,
    readonly conversationId: string,
    readonly phone: string,
  ) {}

  static async start(country: "ES" | "MX", number = phone(country)): Promise<Candidate> {
    const api = await newRequest.newContext({ baseURL: BASE_URL });
    const response = await api.post("/api/chat/conversations", {
      data: { country, locale: country === "ES" ? "es-ES" : "es-MX", phone: number, client: "test_chat" },
    });
    expect(response.status(), await response.text()).toBe(201);
    return new Candidate(api, (await response.json()).conversation_id, number);
  }

  /** Send one message and wait until the agent's turn is over. Returns the agent's replies. */
  async say(text: string): Promise<string[]> {
    const response = await this.api.post(`/api/chat/conversations/${this.conversationId}/messages`, {
      data: { message_id: crypto.randomUUID(), text },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return this.waitForTurn();
  }

  async waitForTurn(timeoutMs = 60_000): Promise<string[]> {
    const replies: string[] = [];
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const response = await this.api.get(`/api/chat/conversations/${this.conversationId}/messages`, {
        params: { after: this.cursor, wait: 10 },
        timeout: 20_000,
      });
      expect(response.ok()).toBeTruthy();
      const page = (await response.json()) as ChatPage;
      this.transcript.push(...page.messages);
      replies.push(...page.messages.filter((m) => m.direction === "outbound").map((m) => m.text));
      this.cursor = Math.max(this.cursor, page.cursor);
      if (page.run_finished) return replies;
    }
    throw new Error(`no end of the agent's turn within ${timeoutMs} ms`);
  }

  /** Every message the Candidate has received so far (reads the rest of the conversation). */
  async inbox(): Promise<string[]> {
    const response = await this.api.get(`/api/chat/conversations/${this.conversationId}/messages`, {
      params: { after: 0 },
    });
    const page = (await response.json()) as ChatPage;
    return page.messages.filter((m) => m.direction === "outbound").map((m) => m.text);
  }

  async close(): Promise<void> {
    await this.api.dispose();
  }
}

/** The persona's messages the stub model reads, in the order the backend asks, from the name to
 * the plain yes at the summary. A Candidate who says all of them is Qualified in Puebla. */
export function screeningAnswers(): string[] {
  return [
    candidateName(),
    "Centro, Puebla",
    "Sí, tengo licencia de manejo",
    "tiempo completo",
    "flexible",
    "2 años",
    "DiDi Food",
    "el lunes",
    "Sí",
  ];
}

/** An API client logged in as a seeded user (for setup and checks, not for what is under test). */
export async function apiAs(email: string): Promise<APIRequestContext> {
  const api = await newRequest.newContext({ baseURL: BASE_URL });
  const response = await api.post("/api/auth/login", { data: { email, password: PASSWORD } });
  expect(response.status(), await response.text()).toBe(200);
  return api;
}

export interface AppSummary {
  id: string;
  stage: string;
  status: string;
  candidate: { full_name: string | null; phone: string | null };
}

/** The Candidate's Application, once the backend has one for this phone. */
export async function applicationFor(api: APIRequestContext, number: string, stage?: string): Promise<AppSummary> {
  let found: AppSummary | undefined;
  await expect
    .poll(
      async () => {
        const response = await api.get("/api/applications", { params: { q: number } });
        const items = ((await response.json()).items ?? []) as AppSummary[];
        found = items.find((a) => a.candidate.phone === number && (!stage || a.stage === stage));
        return Boolean(found);
      },
      { timeout: 30_000, message: `an Application for ${number}${stage ? ` at ${stage}` : ""}` },
    )
    .toBe(true);
  return found!;
}

/** Where `/` lands a seeded user: the Operator seed (@criba.example) on /overview, the Recruiter
 * seeds (recruiter.<city>@sazon.example, criba.db.seed) on /today. Any other address throws. */
export function landingFor(email: string): RegExp {
  if (email.endsWith("@criba.example")) return /\/overview$/;
  if (email.endsWith("@sazon.example")) return /\/today$/;
  throw new Error(`no known landing for ${email}: pass { landing }`);
}

/** Log in through the form, check the landing page, then open Applications (where the existing
 * specs start). `landing` overrides the landing expected from the seeded address. */
export async function logIn(page: Page, email: string, { landing }: { landing?: string | RegExp } = {}): Promise<void> {
  const expected =
    landing === undefined
      ? landingFor(email)
      : typeof landing === "string"
        ? new RegExp(`${landing.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`)
        : landing;
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(expected);
  await page.goto("/applications");
  await expect(page).toHaveURL(/\/applications$/);
}

/** Operator-only keys (contracts/api.openapi.yaml `x-roles: [operator]`) and the usage figures. */
export const COST_KEYS = new Set([
  "cost",
  "cost_usd",
  "est_cost_usd",
  "reconciled_cost_usd",
  "input_tokens",
  "output_tokens",
  "cache_read_tokens",
  "cache_write_tokens",
  "model_mix",
  "spend_by_environment",
]);

export function costKeysIn(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) for (const item of value) costKeysIn(item, found);
  else if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) {
      if (COST_KEYS.has(key)) found.push(key);
      costKeysIn(inner, found);
    }
  }
  return found;
}
