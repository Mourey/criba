// The voice page, /voice (ADR 0059, owner decision 65): voice is its own flow, so these tests moved here
// from testchat.spec.ts and now drive /voice instead of a voice button on /test-chat. They run on the sim
// stack with only the ElevenLabs SDK faked: no request reaches ElevenLabs or LiveKit.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { expect, request as newRequest, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { VOICE_COPY } from "../../src/lib/labels/voice.ts";
import { expectNoA11yViolations } from "./a11y.ts";
import { BASE_URL, requireStack } from "./support.ts";

requireStack();

// --- The voice page with only the SDK faked (spec-voice-agent.web-proof WV13, moved from testchat.spec.ts) ---
//
// The built transport chunk (elevenlabsTransport-*.js, the only module that imports the SDK) is
// replaced by a fake ES module with the same `start(session, callbacks)` export. The fake keeps the
// callbacks on window.__cribaVoiceFake (never the session), counts end() calls and logs nothing.
// Every request to an ElevenLabs or LiveKit host is aborted and counted, every such WebSocket is
// closed and counted, and every socket the page opens at all is counted: each test ends with the
// fake route hit and all three counts at 0. Everything else is the real sim stack.

const VOICE = VOICE_COPY["es-MX"];

const FAKE_TRANSPORT = `
export async function start(_session, callbacks) {
  const fake = (window.__cribaVoiceFake ??= { starts: 0, ends: 0, current: null });
  fake.starts += 1;
  fake.current = callbacks;
  setTimeout(() => {
    callbacks.onStatusChange("connected");
    callbacks.onConnect();
    callbacks.onModeChange("listening");
  }, 0);
  return {
    end: async () => {
      fake.ends += 1;
    },
  };
}
`;

interface VoiceNet {
  fakeHits: number;
  aborted: number;
  closedSockets: number;
  openedSockets: number;
}

/** Install the fake transport, the host aborts and the socket counters, and a silent microphone. */
async function fakeVoice(page: Page): Promise<VoiceNet> {
  const net: VoiceNet = { fakeHits: 0, aborted: 0, closedSockets: 0, openedSockets: 0 };
  await page.addInitScript(() => {
    // Headless Chromium has no microphone: a silent stream stands in for it.
    const silent = async () => new AudioContext().createMediaStreamDestination().stream;
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = silent;
    else Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: silent }, configurable: true });
  });
  await page.route("**/assets/elevenlabsTransport-*.js", async (route) => {
    net.fakeHits += 1;
    await route.fulfill({ status: 200, contentType: "text/javascript", body: FAKE_TRANSPORT });
  });
  await page.route(
    (url) => url.hostname.endsWith("elevenlabs.io") || url.hostname.includes("livekit"),
    async (route) => {
      net.aborted += 1;
      await route.abort();
    },
  );
  await page.routeWebSocket(/elevenlabs\.io|livekit/, async (ws) => {
    net.closedSockets += 1;
    await ws.close();
  });
  page.on("websocket", () => {
    net.openedSockets += 1;
  });
  return net;
}

function expectSdkOnlyFaked(net: VoiceNet): void {
  expect(net.fakeHits, "the fake transport route was hit").toBeGreaterThanOrEqual(1);
  expect(net.aborted, "requests to ElevenLabs or LiveKit hosts").toBe(0);
  expect(net.closedSockets, "WebSockets to ElevenLabs or LiveKit hosts").toBe(0);
  expect(net.openedSockets, "WebSockets opened at all").toBe(0);
}

const region = (page: Page) => page.getByRole("region", { name: VOICE.region });
const voiceStatus = (page: Page) => region(page).getByRole("status");
const captions = (page: Page) => page.getByRole("log", { name: VOICE.captions });

/** Tap Hablar: one call (and, on a fresh page, one voice conversation), listening. Returns the minted
 * voice token, read from the response on the test side only, for `sayAloud`. */
async function callAndListen(page: Page): Promise<string> {
  const minted = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/voice-session"));
  await page.getByRole("button", { name: VOICE.talk }).click();
  const session = (await (await minted).json()) as { voice_token: string };
  await expect(voiceStatus(page)).toHaveText(VOICE.states.listening);
  return session.voice_token;
}

/** The custom-LLM bearer, beside the seed password file. Held by the test runner only, never the page. */
function voiceLlmSecret(): string {
  const seed = process.env.CRIBA_E2E_SEED_PASSWORD_FILE ?? "";
  return readFileSync(join(dirname(seed), "criba_voice_llm_secret.txt"), "utf8").trim();
}

/**
 * One spoken turn, as ElevenLabs would send it: the Candidate's words to the custom-LLM endpoint with the
 * call's voice token, read to the end of the stream. The fake transport carries no audio, so this is how a
 * turn reaches the backend; the page then shows both sides as captions by long poll.
 */
async function sayAloud(voiceToken: string, text: string): Promise<void> {
  const api = await newRequest.newContext({ baseURL: BASE_URL });
  try {
    const response = await api.post("/api/voice/v1/chat/completions", {
      headers: { Authorization: `Bearer ${voiceLlmSecret()}`, Accept: "text/event-stream" },
      data: { model: "criba-voice", stream: true, messages: [{ role: "user", content: text }], elevenlabs_extra_body: { criba_voice_token: voiceToken } },
      timeout: 90_000,
    });
    expect(response.status(), "the spoken turn").toBe(200);
    expect(await response.text()).toContain("[DONE]");
  } finally {
    await api.dispose();
  }
}

const HELLO = "Hola, quiero trabajar de repartidor";

test.describe("voice page, SDK faked", () => {
  let net: VoiceNet;

  test.beforeEach(async ({ page }) => {
    net = await fakeVoice(page);
  });

  test("one tap starts a voice conversation and a call, a spoken turn arrives as captions, and Esc ends it", async ({ page }) => {
    await page.goto("/voice");
    await expect(page.getByText(VOICE.notice)).toBeVisible();
    await expect(page.getByRole("textbox")).toHaveCount(0);
    const started = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/chat/conversations"));
    const voiceToken = await callAndListen(page);
    const conversation = (await (await started).json()) as { conversation_id: string; channel?: string };
    expect(conversation.conversation_id.startsWith("wv_")).toBe(true);
    expect(conversation.channel).toBe("voice");

    const control = page.getByRole("button", { name: VOICE.hangUp });
    await expect(control).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("radio", { name: VOICE.countries.MX })).toBeDisabled();
    // A spoken turn reaches the captions by long poll: the Candidate's words, then the agent's reply.
    await sayAloud(voiceToken, HELLO);
    await expect(captions(page).locator('.voicecall__line[data-side="candidate"]')).toContainText(HELLO, { timeout: 30_000 });
    await expect(captions(page).locator('.voicecall__line[data-side="agent"]')).not.toHaveCount(0, { timeout: 30_000 });
    const sides = await captions(page).locator(".voicecall__line").evaluateAll((lines) => lines.map((l) => l.getAttribute("data-side")));
    expect(sides.indexOf("candidate")).toBeLessThan(sides.lastIndexOf("agent"));
    await expect(captions(page).getByRole("button")).toHaveCount(0);

    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: VOICE.talk })).toHaveAttribute("aria-pressed", "false");
    await expect(voiceStatus(page)).toHaveText(VOICE.states.ended);
    expect(await page.evaluate(() => (window as unknown as { __cribaVoiceFake: { ends: number } }).__cribaVoiceFake.ends)).toBe(1);
    await expect(page.getByRole("button", { name: VOICE.talk })).toBeFocused();
    expectSdkOnlyFaked(net);
  });

  test("no token reaches the console or storage after a call", async ({ page }) => {
    const consoleLines: string[] = [];
    page.on("console", (message) => consoleLines.push(message.text()));
    await page.goto("/voice");

    const minted = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/voice-session"));
    await callAndListen(page);
    const session = (await (await minted).json()) as { conversation_token: string; voice_token: string };
    const tokens = [session.conversation_token, session.voice_token];
    for (const token of tokens) expect(token.length).toBeGreaterThan(8);

    await page.getByRole("button", { name: VOICE.hangUp }).click();
    await expect(voiceStatus(page)).toHaveText(VOICE.states.ended);
    const stored = await page.evaluate(() => {
      const values: string[] = [];
      for (const store of [window.localStorage, window.sessionStorage]) {
        for (let i = 0; i < store.length; i += 1) {
          const key = store.key(i);
          if (key !== null) values.push(key, store.getItem(key) ?? "");
        }
      }
      return values;
    });
    const html = await page.content();
    for (const token of tokens) {
      expect(consoleLines.filter((line) => line.includes(token)), "console lines carrying a token").toHaveLength(0);
      expect(stored.filter((value) => value.includes(token)), "storage values carrying a token").toHaveLength(0);
      expect(html.includes(token), "the DOM carrying a token").toBe(false);
    }
    expectSdkOnlyFaked(net);
  });

  test.describe("on a phone", () => {
    test.use({ viewport: { width: 360, height: 800 }, hasTouch: true, isMobile: true });

    test("at 360 px nothing scrolls sideways and every target is 44 px", async ({ page }) => {
      await page.goto("/voice");
      await sayAloud(await callAndListen(page), HELLO);
      await expect(captions(page).locator('.voicecall__line[data-side="agent"]')).not.toHaveCount(0, { timeout: 30_000 });
      const sizes = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        coarse: window.matchMedia("(pointer: coarse)").matches,
      }));
      expect(sizes.coarse).toBe(true);
      expect(sizes.scrollWidth).toBeLessThanOrEqual(sizes.innerWidth);

      const targets = page.getByRole("main").locator("button:visible, a:visible, label:visible");
      const count = await targets.count();
      expect(count).toBeGreaterThanOrEqual(5);
      for (let i = 0; i < count; i += 1) {
        const target = targets.nth(i);
        const box = await target.boundingBox();
        const label = (await target.textContent())?.trim() ?? "";
        expect(box, label).not.toBeNull();
        expect(box!.width, `${label} width`).toBeGreaterThanOrEqual(44);
        expect(box!.height, `${label} height`).toBeGreaterThanOrEqual(44);
      }
      console.log(`360 px: scrollWidth ${sizes.scrollWidth} <= innerWidth ${sizes.innerWidth}; ${count} targets, all at least 44x44`);
      expectSdkOnlyFaked(net);
    });
  });

  test.describe("under reduced motion", () => {
    test.use({ reducedMotion: "reduce" });

    test("under reduced motion nothing on the page animates, the listening indicator included", async ({ page }) => {
      await page.goto("/voice");
      await callAndListen(page);
      await expect(region(page).locator(".voicecall__listening")).toBeVisible();
      const names = await page.getByRole("main").evaluate((root) =>
        [root, ...root.querySelectorAll("*")].map((el) => getComputedStyle(el).animationName),
      );
      expect(names.length).toBeGreaterThan(1);
      expect(names.filter((name) => name !== "none")).toEqual([]);
      expectSdkOnlyFaked(net);
    });
  });

  test("axe finds nothing in idle, listening, ended and connection lost, in both themes", async ({ page }) => {
    // First conversation: idle (before the first tap), listening (one session), ended.
    await page.goto("/voice");
    await expectNoA11yViolations(page);
    await sayAloud(await callAndListen(page), HELLO);
    await expect(captions(page).locator('.voicecall__line[data-side="agent"]')).not.toHaveCount(0, { timeout: 30_000 });
    await expectNoA11yViolations(page);
    await page.getByRole("button", { name: VOICE.hangUp }).click();
    await expect(voiceStatus(page)).toHaveText(VOICE.states.ended);
    await expectNoA11yViolations(page);

    // Second conversation: an error disconnect, the one reconnect (a second session), another error.
    await page.getByRole("button", { name: VOICE.restart }).click();
    await expect(page.getByRole("radio", { name: VOICE.countries.MX })).toBeEnabled();
    await callAndListen(page);
    const fail = () => page.evaluate(() => (window as unknown as { __cribaVoiceFake: { current: { onDisconnect: (r: string) => void } } }).__cribaVoiceFake.current.onDisconnect("error"));
    await fail();
    await expect(voiceStatus(page)).toHaveText(VOICE.states.listening);
    expect(await page.evaluate(() => (window as unknown as { __cribaVoiceFake: { starts: number } }).__cribaVoiceFake.starts)).toBe(3);
    await fail();
    await expect(voiceStatus(page)).toHaveText(VOICE.states.connectionLost);
    await expectNoA11yViolations(page);
    expectSdkOnlyFaked(net);
  });
});

test("the test chat is text only: no voice control (owner decision 65)", async ({ page }) => {
  await page.goto("/test-chat");
  await page.getByRole("button", { name: "Start conversation" }).click();
  await expect(page.getByRole("log", { name: "Messages" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^(Hablar|Colgar)$/ })).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveCount(0);
});
