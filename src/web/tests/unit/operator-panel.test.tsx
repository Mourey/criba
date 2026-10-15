import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AppEvent, Message, MessageSignal } from "../../src/api/types.ts";
import { stageLabel } from "../../src/lib/labels.ts";
import { detail, operatorMe, recruiterMe } from "./fixtures.ts";
import { mockApi, renderRoute } from "./helpers.tsx";
import type { JevReading, JevReadingEntry, JevReadings, JevThresholds } from "../../src/api/types.ts";
import type { ToolCall, ToolCallRun, ToolCallTrace } from "../../src/api/types.ts";
import { formatDateTime } from "../../src/lib/format.ts";

const ID = "app_01JA2B3C4D5E6F7G8H9J0K";
const FIXED_LINE = "Jev's reading of each typed message. Not an assessment of the Candidate.";

class FakeEventSource {
  listeners = new Map<string, ((e: Event) => void)[]>();
  constructor(public url: string) {}
  addEventListener(name: string, fn: (e: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  removeEventListener() {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

const typed = (id: number, body: string, minute: number): Message => ({
  id,
  direction: "inbound",
  sender: "candidate",
  sender_name: null,
  content_type: "text",
  body,
  interactive: null,
  created_at: `2026-10-09T10:${String(minute).padStart(2, "0")}:00Z`,
});

// Three more typed Candidate messages after the fixture's own (ids 1, 2 and 9).
const messages: Message[] = [
  ...detail().messages,
  typed(20, "no entiendo nada de esto", 10),
  typed(21, "estoy harto, ya van tres veces", 11),
  typed(22, "Quiero hablar con una persona", 12),
];

const signal = (message_id: number, stage: MessageSignal["stage"], minute: number, extra: Partial<MessageSignal> = {}): MessageSignal => ({
  message_id,
  stage,
  at: `2026-10-09T10:${String(minute).padStart(2, "0")}:05Z`,
  fired: [],
  language_changed: false,
  escalated: false,
  ...extra,
});

// Five entries, out of Transcript order; 999 is not in the loaded Transcript (sorted by `at`, after the rest).
const fiveSignals: MessageSignal[] = [
  signal(22, "driver_license", 12, { fired: ["wants_human"], escalated: true }),
  signal(1, "greeting", 0, { fired: ["confused"] }),
  signal(21, "zone", 11, { fired: ["frustrated", "confused"] }),
  signal(20, "name", 10, { fired: ["confused"], language_changed: true }),
  signal(999, "availability", 1, { fired: ["frustrated"] }),
];

const runError = {
  id: 30,
  kind: "run_error",
  actor: "system",
  user_name: null,
  data: { kind: "timeout", run_id: "run_7f3a", system_turn: true, text: "The model said something private here" },
  created_at: "2026-10-09T10:20:00Z",
} as unknown as AppEvent;

const withSignals = (message_signals: MessageSignal[] | undefined, events: AppEvent[] = detail().events) => {
  const base = detail({ messages, events });
  return message_signals === undefined ? base : { ...base, message_signals };
};

const panel = () => screen.findByRole("region", { name: "Operator panel" });
const rows = (p: HTMLElement) => within(within(p).getByRole("list", { name: "Messages with a signal" })).getAllByRole("listitem");

// Jev readings (the jev-readings child): the response of GET /api/applications/:id/jev-readings.
const THRESHOLDS: JevThresholds = {
  version: "jev-1.13-v3",
  ask_confidence: 0.8,
  first_confidence: 0.7,
  switch_confidence: 0.6,
  switch_noul: 0.85,
  frustrated: 0.8,
  confused: 0.75,
  wants_human_hint: 0.7,
  wants_human_escalate: 0.85,
  abusive_escalate: 0.85,
  readable: 0.3,
  unreadable: 0.6,
};

const jevReading = (extra: Partial<JevReading> = {}): JevReading => ({
  lang: "es",
  lang_confidence: 0.99,
  switch: 0.05,
  frustrated: 0.1,
  confused: 0.1,
  wants_human: 0.05,
  abusive: 0.02,
  fired: [],
  hint: null,
  escalation: null,
  model: "typesafe/jev-1.13",
  policy_version: THRESHOLDS.version,
  first_message: false,
  language_set: null,
  ...extra,
});

const readingEntry = (
  message_id: number,
  minute: number,
  status: JevReadingEntry["status"] = "ok",
  extra: Partial<JevReading> | null = {},
): JevReadingEntry => ({
  message_id,
  at: `2026-10-09T10:${String(minute).padStart(2, "0")}:00Z`,
  stage: "greeting",
  status,
  reading: status === "ok" && extra !== null ? jevReading(extra) : null,
});

const jevReadings = (readings: JevReadingEntry[], thresholds: JevThresholds = THRESHOLDS): JevReadings => ({
  application_id: ID,
  thresholds,
  readings,
});

// Eight typed Candidate messages in the Transcript (ids 1, 20 to 26); the readings come back in reverse
// order, and 999 is a typed message that is not in the loaded Transcript.
const readingMessages: Message[] = [
  ...messages,
  typed(23, "mensaje que dio error", 13),
  typed(24, "mensaje que agoto el tiempo", 14),
  typed(25, "mensaje viejo ya borrado", 15),
  typed(26, "mensaje que no se leyo", 16),
];

const mixedReadings: JevReadingEntry[] = [
  readingEntry(1, 0, "ok", { first_message: true, language_set: "es-MX", lang_confidence: 0.99 }),
  readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"], hint: "simpler", lang_confidence: 0.55 }),
  readingEntry(21, 11, "ok", {
    lang: "en",
    lang_confidence: 0.9,
    language_set: "en",
    frustrated: 0.91,
    confused: 0.4,
    fired: ["frustrated"],
    hint: "apologise",
  }),
  readingEntry(22, 12, "ok", {
    wants_human: 0.91,
    switch: 0.2,
    fired: ["wants_human"],
    escalation: "wants_human",
    hint: "escalated",
  }),
  readingEntry(23, 13, "error"),
  readingEntry(24, 14, "timeout"),
  readingEntry(25, 15, "expired"),
  readingEntry(26, 16, "not_read"),
  readingEntry(999, 17, "not_read"),
];

const withReadings = (message_signals: MessageSignal[] = []) => ({ ...detail({ messages: readingMessages }), message_signals });
const READINGS_PATH = "GET /api/applications/:id/jev-readings";
const mockWithReadings = (entries: JevReadingEntry[] = mixedReadings, thresholds: JevThresholds = THRESHOLDS) =>
  mockApi({
    "GET /api/auth/me": operatorMe,
    [`GET /api/applications/:id`]: withReadings(),
    [READINGS_PATH]: jevReadings([...entries].reverse(), thresholds),
  });
const readingsList = (p: HTMLElement) => within(p).findByRole("list", { name: "Jev readings by message" });
const readingRows = async (p: HTMLElement) => within(await readingsList(p)).getAllByRole("listitem");
// Row positions in Transcript order: message 1, then 20 to 26, then 999 (not in the loaded Transcript).
const ROW = { first: 0, confused: 1, frustrated: 2, wantsHuman: 3, error: 4, timeout: 5, expired: 6, notRead: 7, outside: 8 } as const;

describe("Operator panel: who sees it (UI8)", () => {
  it("shows an Operator the panel and its toggle, open by default", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const toggle = screen.getByRole("button", { name: "Operator panel" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(region.id).not.toBe("");
    expect(toggle).toHaveAttribute("aria-controls", region.id);
    expect(region.tagName).toBe("SECTION");
  });

  it("never shows the panel, its toggle or its text to a Recruiter", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Operator panel/)).not.toBeInTheDocument();
    expect(screen.queryByText(FIXED_LINE)).not.toBeInTheDocument();
  });

  it("hides the panel and its toggle while an Operator previews the Recruiter view", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    await panel();
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Operator panel/)).not.toBeInTheDocument();
  });

  it("closes and reopens from the toggle, which keeps the focus and flips aria-expanded", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    const { container } = renderRoute(`/applications/${ID}`);
    const region = await panel();
    const id = region.id;
    const toggle = screen.getByRole("button", { name: "Operator panel" });
    expect(container.querySelector(".detail-columns--panel")).not.toBeNull();
    toggle.focus();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", id);
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(container.querySelector(".detail-columns--panel")).toBeNull();
    expect(toggle).toHaveFocus();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(await panel()).toHaveAttribute("id", id);
    expect(toggle).toHaveFocus();
  });

  it("makes no request of its own: only the session and the cached detail are fetched", async () => {
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals, [...detail().events, runError]),
      // Never answers, so the flag rows this case reads stay on screen however fast the readings would come.
      [`GET /api/applications/:id/jev-readings`]: () => new Promise<JevReadings>(() => {}),
      [`GET /api/applications/:id/tool-calls`]: () => new Promise<ToolCallTrace>(() => {}),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    fireEvent.click(within(rows(region)[1]).getByRole("button", { name: "Show in Transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Operator panel" }));
    fireEvent.click(screen.getByRole("button", { name: "Operator panel" }));
    await panel();
    const paths = new Set(mock.calls.map((c) => `${c.method} ${c.path}`));
    expect([...paths].sort()).toEqual([
      "GET /api/applications/" + ID,
      "GET /api/applications/" + ID + "/jev-readings",
      "GET /api/applications/" + ID + "/tool-calls",
      "GET /api/auth/me",
    ]);
    expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(1);
  });
});

describe("Operator panel: content (UI9)", () => {
  it("opens with the fixed line and counts each signal, zero included", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(within(region).getByText(FIXED_LINE)).toBeInTheDocument();
    const counts = within(within(region).getByRole("list", { name: "Signal counts" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(counts).toEqual(["Frustrated 2", "Confused 3", "Wants a person 1", "Abusive 0"]);
  });

  it("gives one row per entry, 5 of 5, in Transcript order, with Tags, language and Escalation marks", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const list = rows(await panel());
    expect(list).toHaveLength(5);
    expect(list.map((li) => [stageLabel.greeting, stageLabel.name, stageLabel.zone, stageLabel.driver_license, stageLabel.availability].find((s) => li.textContent?.includes(s)))).toEqual([
      stageLabel.greeting,
      stageLabel.name,
      stageLabel.zone,
      stageLabel.driver_license,
      stageLabel.availability,
    ]);
    expect(within(list[0]).getByText("Confused")).toHaveClass("ui-tag");
    expect(within(list[1]).getByText("Language changed")).toBeInTheDocument();
    expect(within(list[1]).queryByText("Escalation raised")).not.toBeInTheDocument();
    expect(within(list[2]).getByText("Frustrated")).toBeInTheDocument();
    expect(within(list[2]).getByText("Confused")).toBeInTheDocument();
    expect(within(list[3]).getByText("Wants a person")).toBeInTheDocument();
    expect(within(list[3]).getByText("Escalation raised")).toBeInTheDocument();
    expect(within(list[3]).queryByText("Language changed")).not.toBeInTheDocument();
    expect(within(list[4]).getByText("Frustrated")).toBeInTheDocument();
  });

  it("says the server does not return signals when the field is absent, not that none fired", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(undefined) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(within(region).getByText("This server does not return signals.")).toBeInTheDocument();
    expect(within(region).queryByText("No signal fired on this Application.")).not.toBeInTheDocument();
    expect(within(region).queryByRole("list", { name: "Messages with a signal" })).not.toBeInTheDocument();
  });

  it("shows zero counts and says no signal fired when the list is empty", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals([]) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(within(region).getByText("No signal fired on this Application.")).toBeInTheDocument();
    expect(within(region).queryByText("This server does not return signals.")).not.toBeInTheDocument();
    const counts = within(within(region).getByRole("list", { name: "Signal counts" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(counts).toEqual(["Frustrated 0", "Confused 0", "Wants a person 0", "Abusive 0"]);
  });

  it("Show in Transcript scrolls the log to the message and marks its bubble until another row is chosen", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: scrollIntoView, configurable: true, writable: true });
    try {
      mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
      renderRoute(`/applications/${ID}`);
      const list = rows(await panel());
      const log = screen.getByRole("log", { name: "Transcript" });
      const bubble = (id: number) => log.querySelector(`[data-message-id="${id}"]`) as HTMLElement;
      expect(bubble(20)).not.toBeNull();
      expect(log.querySelector("[data-selected]")).toBeNull();
      log.getBoundingClientRect = () => ({ top: 100, bottom: 600, left: 0, right: 0, width: 0, height: 500, x: 0, y: 100, toJSON: () => ({}) });
      bubble(20).getBoundingClientRect = () => ({ top: 340, bottom: 380, left: 0, right: 0, width: 0, height: 40, x: 0, y: 340, toJSON: () => ({}) });
      log.scrollTop = 0;

      fireEvent.click(within(list[1]).getByRole("button", { name: "Show in Transcript" }));
      await waitFor(() => expect(bubble(20)).toHaveAttribute("data-selected"));
      expect(log.scrollTop).toBe(240);
      expect(log.querySelectorAll("[data-selected]")).toHaveLength(1);

      fireEvent.click(within(list[2]).getByRole("button", { name: "Show in Transcript" }));
      await waitFor(() => expect(bubble(21)).toHaveAttribute("data-selected"));
      expect(bubble(20)).not.toHaveAttribute("data-selected");
      expect(log.querySelectorAll("[data-selected]")).toHaveLength(1);
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("disables Show in Transcript for a message that is not in the loaded Transcript, and keeps its row", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const list = rows(await panel());
    expect(within(list[4]).getByRole("button", { name: "Show in Transcript" })).toBeDisabled();
    for (const row of list.slice(0, 4)) expect(within(row).getByRole("button", { name: "Show in Transcript" })).toBeEnabled();
  });

  it("lists agent run errors by time, kind, system turn and run id, never other data keys", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals, [...detail().events, runError]) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const errors = within(region).getByRole("heading", { name: "Agent run errors" }).closest("section") as HTMLElement;
    expect(errors).not.toBeNull();
    const items = within(errors).getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("Timeout");
    expect(items[0]).toHaveTextContent("System turn");
    expect(items[0]).toHaveTextContent("run_7f3a");
    expect(within(items[0]).getByText((_, el) => el?.tagName === "TIME")).toHaveAttribute("datetime", "2026-10-09T10:20:00Z");
    expect(region).not.toHaveTextContent("The model said something private here");
    expect(document.body).not.toHaveTextContent("The model said something private here");
  });

  it("says when there is no agent run error", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const errors = within(region).getByRole("heading", { name: "Agent run errors" }).closest("section") as HTMLElement;
    expect(errors).toHaveTextContent("No agent run errors on this Application.");
  });

  it("never repeats a message's text inside the panel", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals, [...detail().events, runError]) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const log = screen.getByRole("log", { name: "Transcript" });
    for (const m of messages) {
      if (!m.body) continue;
      expect(within(log).getByText(m.body)).toBeInTheDocument();
      expect(region.textContent).not.toContain(m.body);
    }
  });

  it("holds the cost block with its heading, labels and values", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(within(region).getByRole("heading", { name: "Cost (Operators only)" })).toBeInTheDocument();
    expect(within(region).getByText("$0.0031")).toBeInTheDocument();
    expect(within(region).getByText("Estimated cost")).toBeInTheDocument();
    expect(within(region).getByText("Tokens in / out")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Cost (Operators only)" })).toHaveLength(1);
  });
});

describe("Operator panel: Jev readings (JR2, JR4)", () => {
  const meter = (row: HTMLElement, name: RegExp | string) => within(row).getByRole("meter", { name });

  it("draws each bar's threshold markers when the policy version matches, and names value and threshold", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const wants = list[ROW.wantsHuman];
    expect(meter(wants, "Wants a person 0.91, hint threshold 0.70, Escalation threshold 0.85")).toBeInTheDocument();
    expect(within(meter(wants, /^Wants a person/)).getByText("hint threshold 0.70")).toBeInTheDocument();
    expect(within(meter(wants, /^Wants a person/)).getByText("Escalation threshold 0.85")).toBeInTheDocument();
    expect(meter(wants, /^Language switch/)).toHaveAccessibleName("Language switch 0.20, threshold 0.85");
    expect(meter(wants, /^Abusive/)).toHaveAccessibleName("Abusive 0.02, threshold 0.85");
    expect(meter(wants, /^Frustrated/)).toHaveAccessibleName("Frustrated 0.10, threshold 0.80");
    const confused = meter(list[ROW.confused], "Confused 0.97, threshold 0.75");
    expect(within(confused).getByText("threshold 0.75")).toBeInTheDocument();
    expect(confused).not.toHaveAttribute("role", "img");
    expect(within(confused).getByText("0.97")).toBeInTheDocument();
  });

  it("draws no marker for another policy version, and says its thresholds are not available", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { policy_version: "jev-1.12-v1", confused: 0.97 }), readingEntry(21, 11, "ok", { policy_version: null })]);
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    expect(list).toHaveLength(2);
    expect(within(list[0]).getByText("Thresholds of policy jev-1.12-v1 are not available.")).toBeInTheDocument();
    expect(within(list[1]).getByText("Thresholds of policy unknown are not available.")).toBeInTheDocument();
    for (const row of list) {
      expect(within(row).getAllByRole("meter")).toHaveLength(5);
      expect(row.textContent).not.toMatch(/threshold \d/);
    }
    expect(meter(list[0], "Confused 0.97")).toBeInTheDocument();
    expect(within(list[0]).getByText("Spanish 0.99")).toBeInTheDocument();
    expect(within(list[0]).queryByText(/Spanish 0\.99, threshold/)).not.toBeInTheDocument();
  });

  it("reads the language as text against the first or switch confidence threshold, never as a bar", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    // First message: first_confidence 0.70; later messages: switch_confidence 0.60.
    expect(within(list[ROW.first]).getByText("Spanish 0.99, threshold 0.70")).toBeInTheDocument();
    expect(within(list[ROW.confused]).getByText("Spanish 0.55, threshold 0.60")).toBeInTheDocument();
    expect(within(list[ROW.frustrated]).getByText("English 0.90, threshold 0.60")).toBeInTheDocument();
    expect(within(list[ROW.first]).queryByRole("meter", { name: /Spanish/ })).not.toBeInTheDocument();
  });

  it("shows an ok row with five bars and their values, the hint code, and the model and policy version", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const row = list[ROW.confused];
    const bars = within(row).getAllByRole("meter");
    expect(bars.map((b) => b.getAttribute("aria-label")?.split(/ \d/)[0])).toEqual(["Language switch", "Frustrated", "Confused", "Wants a person", "Abusive"]);
    expect(bars.map((b) => b.getAttribute("aria-valuenow"))).toEqual(["0.05", "0.1", "0.97", "0.05", "0.02"]);
    for (const v of ["0.05", "0.10", "0.97", "0.05", "0.02"]) expect(within(row).getAllByText(v).length).toBeGreaterThan(0);
    expect(within(row).getByText("Tone hint: simpler")).toBeInTheDocument();
    expect(within(row).getByText("typesafe/jev-1.13 · policy jev-1.13-v3")).toBeInTheDocument();
    // A reading with no hint shows no hint line.
    expect(within(list[ROW.first]).queryByText(/Tone hint/)).not.toBeInTheDocument();
  });

  it("gives a Tag per fired signal, Language changed after the first message only, and Escalation raised", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    // Message 1 stored language_set but is the first message: no "Language changed". It fired nothing.
    expect(within(list[ROW.first]).queryByText("Language changed")).not.toBeInTheDocument();
    for (const t of ["Frustrated", "Confused", "Wants a person", "Abusive", "Escalation raised"]) {
      expect(within(list[ROW.first]).queryByText(t, { selector: ".ui-tag" })).not.toBeInTheDocument();
    }
    expect(within(list[ROW.confused]).getByText("Confused", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[ROW.confused]).queryByText("Language changed")).not.toBeInTheDocument();
    expect(within(list[ROW.frustrated]).getByText("Frustrated", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[ROW.frustrated]).getByText("Language changed")).toBeInTheDocument();
    expect(within(list[ROW.wantsHuman]).getByText("Wants a person", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[ROW.wantsHuman]).getByText("Escalation raised")).toBeInTheDocument();
    expect(within(list[ROW.confused]).queryByText("Escalation raised")).not.toBeInTheDocument();
  });

  it("says what each non-ok status means, verbatim, and draws no bars for it", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const lines: [number, string][] = [
      [ROW.error, "Jev could not read this message (error)"],
      [ROW.timeout, "Jev could not read this message (timed out)"],
      [ROW.expired, "Jev's reading was deleted after the retention period."],
      [ROW.notRead, "Jev did not read this message (Jev off, a Takeover, an opt-out or a failure)."],
    ];
    for (const [i, line] of lines) {
      expect(within(list[i]).getByText(line)).toBeInTheDocument();
      expect(within(list[i]).queryByRole("meter")).not.toBeInTheDocument();
      expect(within(list[i]).queryByText(/Tone hint|threshold/)).not.toBeInTheDocument();
    }
  });

  it("lists one row per entry, calm and non-ok ones included, in Transcript order, with Show in Transcript", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const list = await readingRows(region);
    expect(list).toHaveLength(9);
    expect(within(region).queryByRole("list", { name: "Messages with a signal" })).not.toBeInTheDocument();
    const log = screen.getByRole("log", { name: "Transcript" });
    const bubble = (id: number) => log.querySelector(`[data-message-id="${id}"]`) as HTMLElement;
    fireEvent.click(within(list[ROW.error]).getByRole("button", { name: "Show in Transcript" }));
    await waitFor(() => expect(bubble(23)).toHaveAttribute("data-selected"));
    fireEvent.click(within(list[ROW.first]).getByRole("button", { name: "Show in Transcript" }));
    await waitFor(() => expect(bubble(1)).toHaveAttribute("data-selected"));
    expect(bubble(23)).not.toHaveAttribute("data-selected");
    expect(within(list[ROW.outside]).getByRole("button", { name: "Show in Transcript" })).toBeDisabled();
    for (const row of list.slice(0, ROW.outside)) expect(within(row).getByRole("button", { name: "Show in Transcript" })).toBeEnabled();
  });

  it("keeps the counts and adds the peak of each signal over ok readings only, with a dash when there is none", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withReadings([signal(20, "name", 10, { fired: ["confused"] }), signal(21, "zone", 11, { fired: ["frustrated"] })]),
      [READINGS_PATH]: jevReadings([
        readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"] }),
        readingEntry(21, 11, "ok", { confused: 0.4, frustrated: 0.91, fired: ["frustrated"] }),
        readingEntry(22, 12, "ok", { wants_human: 0.3 }),
        readingEntry(23, 13, "error"),
      ]),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    const text = (name: string) =>
      within(within(region).getByRole("list", { name }))
        .getAllByRole("listitem")
        .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(text("Signal counts")).toEqual(["Frustrated 1", "Confused 1", "Wants a person 0", "Abusive 0", "Manipulation -"]);
    expect(text("Peak score per signal")).toEqual(["Frustrated 0.91", "Confused 0.97", "Wants a person 0.30", "Abusive 0.02", "Manipulation -"]);
  });

  it("shows a dash for a signal with no ok reading", async () => {
    mockWithReadings([readingEntry(23, 13, "error"), readingEntry(24, 14, "timeout")]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    const peaks = within(within(region).getByRole("list", { name: "Peak score per signal" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(peaks).toEqual(["Frustrated -", "Confused -", "Wants a person -", "Abusive -", "Manipulation -"]);
  });

  it("falls back to the flag rows with Scores unavailable and Retry when the request fails, and Retry loads the readings", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals),
      [READINGS_PATH]: () => (calls++ === 0 ? [500, { error: "internal", message: "boom" }] : jevReadings(mixedReadings)),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(await within(region).findByText("Scores unavailable")).toBeInTheDocument();
    expect(rows(region)).toHaveLength(5);
    const counts = within(within(region).getByRole("list", { name: "Signal counts" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(counts).toEqual(["Frustrated 2", "Confused 3", "Wants a person 1", "Abusive 0"]);
    expect(within(region).queryByRole("list", { name: "Peak score per signal" })).not.toBeInTheDocument();
    fireEvent.click(within(region).getByRole("button", { name: "Retry" }));
    expect(await readingsList(region)).toBeInTheDocument();
    expect(mock.find("GET", `/api/applications/${ID}/jev-readings`)).toHaveLength(2);
    expect(within(region).queryByText("Scores unavailable")).not.toBeInTheDocument();
    expect(within(region).queryByRole("list", { name: "Messages with a signal" })).not.toBeInTheDocument();
  });

  it("counts from the flag entries before and after the readings load, so a signal on a message the readings omit stays counted", async () => {
    // Message 22 and 999 fired signals but have no entry in the readings (a spoken turn is in message_signals only).
    mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals),
      [READINGS_PATH]: jevReadings([readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"] })]),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const counts = () =>
      within(within(region).getByRole("list", { name: "Signal counts" }))
        .getAllByRole("listitem")
        .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    const before = counts();
    expect(before).toEqual(["Frustrated 2", "Confused 3", "Wants a person 1", "Abusive 0"]);
    await readingsList(region);
    expect(counts()).toEqual([...before, "Manipulation -"]);
  });

  it("keeps the flag row of a message the readings omit (a spoken turn) in the readings list, in Transcript order", async () => {
    // Messages 1, 21, 22 and 999 fired a signal or changed the language but have no reading entry.
    mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals),
      [READINGS_PATH]: jevReadings([readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"] })]),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const list = await readingRows(region);
    expect(list).toHaveLength(1 + 4);
    expect(within(region).queryByRole("list", { name: "Messages with a signal" })).not.toBeInTheDocument();
    // Transcript order: 1, 20 (the reading), 21, 22, then 999 (not in the Transcript).
    expect(within(list[0]).getByText("Confused", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[0]).queryByRole("meter")).not.toBeInTheDocument();
    expect(within(list[1]).getAllByRole("meter")).toHaveLength(5);
    expect(within(list[2]).getByText("Frustrated", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[2]).getByText("Confused", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[3]).getByText("Wants a person", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[3]).getByText("Escalation raised")).toBeInTheDocument();
    expect(within(list[4]).getByText("Frustrated", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(list[4]).getByRole("button", { name: "Show in Transcript" })).toBeDisabled();
    for (const row of list.slice(0, 4)) expect(within(row).getByRole("button", { name: "Show in Transcript" })).toBeEnabled();
  });

  it("makes no readings request for a Recruiter", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: withReadings(),
      [READINGS_PATH]: jevReadings(mixedReadings),
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(mock.calls.filter((c) => c.path.endsWith("/jev-readings"))).toHaveLength(0);
  });

  it("makes no further readings request once an Operator previews the Recruiter view", async () => {
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withReadings(),
      [READINGS_PATH]: jevReadings(mixedReadings),
    });
    renderRoute(`/applications/${ID}`);
    await panel();
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument());
    const before = mock.calls.filter((c) => c.path.endsWith("/jev-readings")).length;
    expect(before).toBeLessThanOrEqual(1);
    await new Promise((r) => setTimeout(r, 50));
    expect(mock.calls.filter((c) => c.path.endsWith("/jev-readings"))).toHaveLength(before);
  });

  it("shows intent as its label and malice as a bar, and never a text value placed in either field", async () => {
    const withExtras = readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"], intent: { choice: "steering", confidence: 0.9 }, malice: 0.99 });
    const extras = { ...withExtras, reading: { ...withExtras.reading, intent: { ...withExtras.reading?.intent, body: "SECRET intent text" } } } as JevReadingEntry;
    mockWithReadings([extras, readingEntry(21, 11, "ok", { confused: 0.97, fired: ["confused"], intent: { choice: "steering", confidence: 0.9 }, malice: 0.99 })]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const list = await readingRows(region);
    expect(list).toHaveLength(2);
    const labels = (row: HTMLElement) => within(row).getAllByRole("meter").map((b) => b.getAttribute("aria-label"));
    expect(labels(list[0])).toEqual(labels(list[1]));
    expect(within(list[0]).getByText("Intent: Steering the agent (0.90)")).toBeInTheDocument();
    expect(region.textContent).not.toContain("SECRET intent text");
    const log = screen.getByRole("log", { name: "Transcript" });
    for (const m of readingMessages) {
      if (!m.body) continue;
      expect(within(log).getByText(m.body)).toBeInTheDocument();
      expect(region.textContent).not.toContain(m.body);
    }
  });
});

describe("Operator panel: reading the bars (critique round 1)", () => {
  it("marks a bar fired from the stored flags only, never from a score against its threshold", async () => {
    // Message 20 fired confused; 21 fired frustrated and changed the language; 22 scored 0.91 on wants_human but
    // stored no fired signal, so its bar stays quiet.
    mockWithReadings([
      readingEntry(20, 10, "ok", { confused: 0.97, fired: ["confused"] }),
      readingEntry(21, 11, "ok", { lang: "en", language_set: "en", frustrated: 0.91, fired: ["frustrated"] }),
      readingEntry(22, 12, "ok", { wants_human: 0.91 }),
      readingEntry(1, 0, "ok", { first_message: true, language_set: "es-MX" }),
    ]);
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const firedBars = (row: HTMLElement) =>
      within(row)
        .getAllByRole("meter")
        .filter((m) => m.classList.contains("op-bar--fired"))
        .map((m) => m.getAttribute("aria-label")?.split(/ \d/)[0]);
    expect(firedBars(list[0])).toEqual([]);
    expect(firedBars(list[1])).toEqual(["Confused"]);
    expect(firedBars(list[2])).toEqual(["Language switch", "Frustrated"]);
    expect(firedBars(list[3])).toEqual([]);
  });

  it("draws the hint marker as a short tick and the Escalation marker as the tall one", async () => {
    mockWithReadings([readingEntry(22, 12, "ok", { wants_human: 0.91, fired: ["wants_human"] })]);
    renderRoute(`/applications/${ID}`);
    const [row] = await readingRows(await panel());
    const wants = within(row).getByRole("meter", { name: /^Wants a person/ });
    const marker = (text: string) => within(wants).getByText(text).closest(".op-bar__marker") as HTMLElement;
    expect(marker("hint threshold 0.70")).toHaveClass("op-bar__marker--hint");
    expect(marker("Escalation threshold 0.85")).not.toHaveClass("op-bar__marker--hint");
    expect(within(row).getByRole("meter", { name: /^Confused/ }).querySelector(".op-bar__marker--hint")).toBeNull();
  });

  it("explains the ticks in one legend line while readings are shown, and not for the flag-row fallback", async () => {
    const legend = "Ticks mark thresholds. Wants a person: short = hint, tall = Escalation.";
    mockWithReadings();
    const first = renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(await within(region).findAllByText(legend)).toHaveLength(1);
    first.unmount();
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: withSignals(fiveSignals) });
    renderRoute(`/applications/${ID}`);
    const fallback = await panel();
    await within(fallback).findByText("Scores unavailable");
    expect(within(fallback).queryByText(legend)).not.toBeInTheDocument();
  });

  it("lines the counts and the peaks up in one grid under Count and Peak captions, the peaks' signal names kept for screen readers only", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    expect(within(region).getByText("Count")).toHaveAttribute("aria-hidden", "true");
    expect(within(region).getByText("Peak")).toHaveAttribute("aria-hidden", "true");
    const counts = within(region).getByRole("list", { name: "Signal counts" });
    const peaks = within(region).getByRole("list", { name: "Peak score per signal" });
    expect(counts.parentElement).toBe(peaks.parentElement);
    for (const name of ["Frustrated", "Confused", "Wants a person", "Abusive", "Manipulation"]) {
      expect(within(counts).getByText(name)).not.toHaveClass("ui-sr-only");
      expect(within(peaks).getByText(name)).toHaveClass("ui-sr-only");
    }
  });

  it("says why Show in Transcript is disabled for a message outside the loaded Transcript", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const off = within(list[ROW.outside]).getByRole("button", { name: "Show in Transcript" });
    expect(off).toBeDisabled();
    expect(off).toHaveAttribute("title", "This message is not in the loaded Transcript.");
    expect(within(list[ROW.first]).getByRole("button", { name: "Show in Transcript" })).not.toHaveAttribute("title");
  });

  it("puts the time, Stage, Tags and Show in Transcript on one head line", async () => {
    mockWithReadings();
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    const head = list[ROW.confused].querySelector(".op-signal__head") as HTMLElement;
    expect(within(head).getByText((_, el) => el?.tagName === "TIME")).toBeInTheDocument();
    expect(within(head).getByText(stageLabel.greeting)).toBeInTheDocument();
    expect(within(head).getByText("Confused", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(within(head).getByRole("button", { name: "Show in Transcript" })).toBeInTheDocument();
  });
});

describe("Operator panel: legend only with ticks (critique round 3)", () => {
  const legend = "Ticks mark thresholds. Wants a person: short = hint, tall = Escalation.";

  it("shows no legend when no reading has the response's policy version, so no tick is drawn", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { policy_version: "jev-1.12-v1" }), readingEntry(21, 11, "ok", { policy_version: null }), readingEntry(23, 13, "error")]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    expect(region.querySelector(".op-bar__marker")).toBeNull();
    expect(within(region).queryByText(legend)).not.toBeInTheDocument();
  });

  it("shows the legend once one reading with a matching policy version draws its ticks", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { policy_version: "jev-1.12-v1" }), readingEntry(21, 11, "ok")]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    expect(region.querySelector(".op-bar__marker")).not.toBeNull();
    expect(within(region).getByText(legend)).toBeInTheDocument();
  });
});

describe("Operator panel: intent and Manipulation (JI9)", () => {
  const MUTED = "No intent or manipulation reading.";
  const meters = (row: HTMLElement) => within(row).getAllByRole("meter");
  const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const CHOICES = [
    ["answering", "Answering", 0.91],
    ["asking", "Asking a question", 0.82],
    ["off_topic", "Off topic", 0.73],
    ["wants_to_stop", "Wants to stop", 0.64],
    ["steering", "Steering the agent", 0.55],
    ["other", "Other", 0.46],
  ] as const;

  it("shows the intent as a plain text line with its label and confidence, after the language line and before the bars, for each of the six choices", async () => {
    mockWithReadings(CHOICES.map(([choice, , confidence], i) => readingEntry(20 + i, 10 + i, "ok", { intent: { choice, confidence }, malice: 0.1 })));
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    expect(list).toHaveLength(6);
    CHOICES.forEach(([, label, confidence], i) => {
      const line = within(list[i]).getByText(`Intent: ${label} (${confidence.toFixed(2)})`);
      expect(line.closest(".ui-tag")).toBeNull();
      expect(line.tagName).toBe("P");
      expect(follows(within(list[i]).getByText(/^Spanish 0\.99/), line)).toBe(true);
      expect(follows(line, list[i].querySelector(".op-bars") as Element)).toBe(true);
      expect(within(list[i]).queryByText(MUTED)).not.toBeInTheDocument();
    });
  });

  it("adds a sixth meter named Manipulation with its value as text, and keeps the first five", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { confused: 0.97, malice: 0.97 })]);
    renderRoute(`/applications/${ID}`);
    const [row] = await readingRows(await panel());
    const bars = meters(row);
    expect(bars).toHaveLength(6);
    expect(bars.slice(0, 5).map((b) => b.getAttribute("aria-label")?.split(/ \d/)[0])).toEqual(["Language switch", "Frustrated", "Confused", "Wants a person", "Abusive"]);
    expect(bars.slice(0, 5).map((b) => b.getAttribute("aria-valuenow"))).toEqual(["0.05", "0.1", "0.97", "0.05", "0.02"]);
    expect(bars[5]).toHaveAccessibleName("Manipulation 0.97");
    expect(bars[5]).toHaveAttribute("aria-valuenow", "0.97");
    expect(bars[5]).toHaveAttribute("aria-valuemin", "0");
    expect(bars[5]).toHaveAttribute("aria-valuemax", "1");
    expect(within(bars[5]).getByText("Manipulation")).toBeInTheDocument();
    expect(within(bars[5]).getByText("0.97", { selector: ".op-bar__value" })).toBeInTheDocument();
  });

  it("never draws a marker or the fired style on the Manipulation bar, even at 0.99 with every signal fired", async () => {
    mockWithReadings([
      readingEntry(20, 10, "ok", {
        malice: 0.99,
        frustrated: 0.95,
        confused: 0.95,
        wants_human: 0.95,
        abusive: 0.95,
        switch: 0.95,
        fired: ["frustrated", "confused", "wants_human", "abusive"],
        language_set: "en",
        escalation: "wants_human",
      }),
    ]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const [row] = await readingRows(region);
    const bars = meters(row);
    expect(bars).toHaveLength(6);
    // The five bars still fire and carry their markers, so the sixth is quiet by design and not by the fixture.
    for (const b of bars.slice(0, 5)) expect(b).toHaveClass("op-bar--fired");
    expect(bars[5]).not.toHaveClass("op-bar--fired");
    expect(bars[5].className).toBe("op-bar op-bar--plain");
    for (const b of bars.slice(0, 5)) expect(b).not.toHaveClass("op-bar--plain");
    expect(bars[5].querySelector(".op-bar__marker")).toBeNull();
    expect(bars[5].textContent).not.toMatch(/threshold/);
    expect(within(region).getAllByText("Ticks mark thresholds. Wants a person: short = hint, tall = Escalation.")).toHaveLength(1);
  });

  it("replaces the intent line and the bar with one muted line when both are null, and when both keys are absent", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { intent: null, malice: null }), readingEntry(21, 11, "ok")]);
    renderRoute(`/applications/${ID}`);
    const list = await readingRows(await panel());
    expect(list).toHaveLength(2);
    for (const row of list) {
      expect(within(row).getAllByText(MUTED)).toHaveLength(1);
      expect(meters(row)).toHaveLength(5);
      expect(row.textContent).not.toContain("Intent:");
      expect(row.textContent).not.toContain("Manipulation");
    }
  });

  it("shows no muted line when only one of the two is missing, and keeps what is present (changed by owner decision 2026-10-12)", async () => {
    mockWithReadings([
      readingEntry(20, 10, "ok", { intent: { choice: "asking", confidence: 0.8 }, malice: null }),
      readingEntry(21, 11, "ok", { intent: null, malice: 0.4 }),
    ]);
    renderRoute(`/applications/${ID}`);
    const [onlyIntent, onlyScore] = await readingRows(await panel());
    expect(within(onlyIntent).getByText("Intent: Asking a question (0.80)")).toBeInTheDocument();
    expect(within(onlyIntent).queryAllByText(MUTED)).toHaveLength(0);
    expect(follows(within(onlyIntent).getByText("Intent: Asking a question (0.80)"), onlyIntent.querySelector(".op-bars") as Element)).toBe(true);
    expect(meters(onlyIntent)).toHaveLength(5);
    expect(within(onlyScore).queryAllByText(MUTED)).toHaveLength(0);
    expect(onlyScore.textContent).not.toContain("Intent:");
    expect(onlyScore.querySelector(".op-bars")).not.toBeNull();
    expect(meters(onlyScore)).toHaveLength(6);
    expect(meters(onlyScore)[5]).toHaveAccessibleName("Manipulation 0.40");
  });

  it("with intent null and a score set shows the Manipulation bar and no intent line and no muted line", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { intent: null, malice: 0.4 })]);
    renderRoute(`/applications/${ID}`);
    const [row] = await readingRows(await panel());
    expect(meters(row)).toHaveLength(6);
    expect(meters(row)[5]).toHaveAccessibleName("Manipulation 0.40");
    expect(row.textContent).not.toContain("Intent:");
    expect(within(row).queryByText(MUTED)).not.toBeInTheDocument();
  });

  it("with an intent set and the score null shows the intent line and no Manipulation bar and no muted line", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { intent: { choice: "asking", confidence: 0.8 }, malice: null })]);
    renderRoute(`/applications/${ID}`);
    const [row] = await readingRows(await panel());
    expect(within(row).getByText("Intent: Asking a question (0.80)")).toBeInTheDocument();
    expect(meters(row)).toHaveLength(5);
    expect(within(row).queryByRole("meter", { name: /Manipulation/ })).not.toBeInTheDocument();
    expect(within(row).queryByText(MUTED)).not.toBeInTheDocument();
  });

  it("with both null shows exactly one muted line, no intent line and no Manipulation bar", async () => {
    mockWithReadings([readingEntry(20, 10, "ok", { intent: null, malice: null })]);
    renderRoute(`/applications/${ID}`);
    const [row] = await readingRows(await panel());
    expect(within(row).getAllByText(MUTED)).toHaveLength(1);
    expect(row.textContent).not.toContain("Intent:");
    expect(row.textContent).not.toContain("Manipulation");
    expect(meters(row)).toHaveLength(5);
  });

  it("ends the peaks with the highest Manipulation score and the counts with a dash, as a fifth row of each", async () => {
    mockWithReadings([
      readingEntry(20, 10, "ok", { malice: 0.02 }),
      readingEntry(21, 11, "ok", { malice: 0.97 }),
      readingEntry(22, 12, "ok", { malice: null }),
      readingEntry(23, 13, "error"),
    ]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    await readingsList(region);
    const counts = within(region).getByRole("list", { name: "Signal counts" });
    const peaks = within(region).getByRole("list", { name: "Peak score per signal" });
    const text = (list: HTMLElement) => within(list).getAllByRole("listitem").map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(text(counts)).toEqual(["Frustrated 0", "Confused 0", "Wants a person 0", "Abusive 0", "Manipulation -"]);
    expect(text(peaks)).toEqual(["Frustrated 0.10", "Confused 0.10", "Wants a person 0.05", "Abusive 0.02", "Manipulation 0.97"]);
    expect(within(counts).getByText("Manipulation")).not.toHaveClass("ui-sr-only");
    expect(within(peaks).getByText("Manipulation")).toHaveClass("ui-sr-only");
  });

  it("shows only the label and the number: a text outside the six choices, a text confidence and a text score read as no reading", async () => {
    // Fixtures cast on purpose: the generated types forbid these shapes, and the panel must hold if a server sends one.
    const odd = (extra: Record<string, unknown>) => {
      const e = readingEntry(20, 10, "ok");
      return { ...e, reading: { ...e.reading, ...extra } } as unknown as JevReadingEntry;
    };
    mockWithReadings([
      { ...odd({ intent: { choice: "SECRET choice text", confidence: 0.9 }, malice: null }), message_id: 20 },
      { ...odd({ intent: { choice: "asking", confidence: "SECRET confidence text" } }), message_id: 21, at: "2026-10-09T10:11:00Z" },
      { ...odd({ intent: null, malice: "SECRET score text" }), message_id: 22, at: "2026-10-09T10:12:00Z" },
    ]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const list = await readingRows(region);
    expect(list).toHaveLength(3);
    for (const row of list) {
      expect(within(row).getAllByText(MUTED)).toHaveLength(1);
      expect(meters(row)).toHaveLength(5);
      expect(row.textContent).not.toContain("Intent:");
    }
    expect(region.textContent).not.toContain("SECRET");
    expect(region.textContent).not.toMatch(/malic/i);
  });

  it("shows neither reading on a spoken turn, which has a flag row and no reading", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals),
      [READINGS_PATH]: jevReadings([readingEntry(20, 10, "ok", { intent: { choice: "other", confidence: 0.7 }, malice: 0.1 })]),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const list = await readingRows(region);
    expect(list).toHaveLength(1 + 4);
    expect(within(list[1]).getByText("Intent: Other (0.70)")).toBeInTheDocument();
    for (const i of [0, 2, 3, 4]) {
      expect(within(list[i]).queryByRole("meter")).not.toBeInTheDocument();
      expect(within(list[i]).queryByText(MUTED)).not.toBeInTheDocument();
      expect(list[i].textContent).not.toMatch(/Intent:|Manipulation/);
    }
  });

  it("gives a Recruiter no panel, no intent or Manipulation text and no readings request", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: withReadings(),
      [READINGS_PATH]: jevReadings([readingEntry(20, 10, "ok", { intent: { choice: "steering", confidence: 0.9 }, malice: 0.97 })]),
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Manipulation|Intent:|No intent or manipulation reading/);
    expect(mock.calls.filter((c) => c.path.endsWith("/jev-readings"))).toHaveLength(0);
  });
});

// The agent trace (agent-trace-web, AT7): the response of GET /api/applications/:id/tool-calls.
const TRACE_PATH = "GET /api/applications/:id/tool-calls";
const NEVER = () => new Promise<JevReadings>(() => {});

const toolCall = (extra: Partial<ToolCall> = {}): ToolCall => ({
  tool: "record_answer",
  started_at: "2026-10-09T10:00:30Z",
  duration_ms: 42,
  outcome: "ok",
  error: null,
  args: {},
  ...extra,
});

const toolRun = (extra: Partial<ToolCallRun> = {}): ToolCallRun => ({
  run_id: "run_a1",
  status: "ok",
  input_tokens: 1200,
  output_tokens: 80,
  cost_usd: 0.0123,
  latency_ms: 2400,
  reply_message_ids: [2],
  calls: [],
  ...extra,
});

const toolTrace = (runs: ToolCallRun[]): ToolCallTrace => ({ application_id: ID, runs });

const mockWithTrace = (runs: ToolCallRun[], events: AppEvent[] = detail().events) =>
  mockApi({
    "GET /api/auth/me": operatorMe,
    [`GET /api/applications/:id`]: withSignals(fiveSignals, events),
    // Never answers, so the flag rows stay as the base renders them.
    [`GET /api/applications/:id/jev-readings`]: NEVER,
    [TRACE_PATH]: toolTrace(runs),
  });

const traceSection = async (p: HTMLElement) => (await within(p).findByRole("heading", { name: "Agent runs" })).closest("section") as HTMLElement;
const runList = (s: HTMLElement) => within(s).findByRole("list", { name: "Runs with their tool calls" });
const runItems = async (s: HTMLElement) => Array.from((await runList(s)).children) as HTMLElement[];
const callItems = (run: HTMLElement) => within(within(run).getByRole("list", { name: "Tool calls" })).getAllByRole("listitem");
const argOf = (call: HTMLElement, name: string) => within(call).getByText(name, { selector: "dt" }).nextElementSibling?.textContent;

describe("Operator panel: the agent trace (AT7)", () => {
  it("lists the runs in the order served, each with its id, status, tokens, cost and latency, and a line per call", async () => {
    const mock = mockWithTrace([
      toolRun({
        run_id: "run_b2",
        calls: [
          toolCall({ tool: "record_answer", started_at: "2026-10-09T10:00:30Z", duration_ms: 42, args: { field: "zone" } }),
          toolCall({ tool: "send_choice", started_at: "2026-10-09T10:00:31Z", duration_ms: 1500 }),
        ],
      }),
      toolRun({
        run_id: "run_a1",
        status: "error",
        input_tokens: 300,
        output_tokens: 20,
        cost_usd: 0.0005,
        latency_ms: 750,
        reply_message_ids: [9],
        calls: [toolCall({ tool: "finish_screening", started_at: "2026-10-09T10:04:00Z", duration_ms: 9, outcome: "error", error: "internal" })],
      }),
    ]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const section = await traceSection(region);
    expect(mock.find("GET", `/api/applications/${ID}/tool-calls`)).toHaveLength(1);
    // After the Agent run errors block and before the Cost card, as a sibling section.
    const errors = within(region).getByRole("heading", { name: "Agent run errors" }).closest("section") as HTMLElement;
    const cost = within(region).getByRole("heading", { name: "Cost (Operators only)" });
    expect(section).not.toBe(errors);
    expect(errors.contains(section)).toBe(false);
    expect(errors.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(section.compareDocumentPosition(cost) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const runs = await runItems(section);
    expect(runs).toHaveLength(2);
    expect(within(runs[0]).getByRole("heading", { name: "Run run_b2" })).toBeInTheDocument();
    expect(within(runs[1]).getByRole("heading", { name: "Run run_a1" })).toBeInTheDocument();
    const head = (run: HTMLElement) => run.querySelector(".op-run__head") as HTMLElement;
    expect(within(head(runs[0])).getByText("OK")).toHaveClass("ui-tag");
    expect(within(head(runs[1])).getByText("Error")).toHaveClass("ui-tag");
    expect(within(runs[0]).getByText("Tokens in 1,200")).toBeInTheDocument();
    expect(within(runs[0]).getByText("Tokens out 80")).toBeInTheDocument();
    expect(within(runs[0]).getByText("Cost $0.0123")).toBeInTheDocument();
    expect(within(runs[0]).getByText("Latency 2.4 s")).toBeInTheDocument();
    expect(within(runs[1]).getByText("Tokens in 300")).toBeInTheDocument();
    expect(within(runs[1]).getByText("Cost $0.0005")).toBeInTheDocument();
    expect(within(runs[1]).getByText("Latency 750 ms")).toBeInTheDocument();

    const first = callItems(runs[0]);
    expect(first).toHaveLength(2);
    expect(within(first[0]).getByText("record_answer")).toBeInTheDocument();
    expect(within(first[0]).getByText(formatDateTime("2026-10-09T10:00:30Z"))).toHaveAttribute("datetime", "2026-10-09T10:00:30Z");
    expect(within(first[0]).getByText("42 ms")).toBeInTheDocument();
    expect(within(first[0]).getByText("OK")).toHaveClass("ui-tag");
    expect(within(first[1]).getByText("send_choice")).toBeInTheDocument();
    expect(within(first[1]).getByText("1.5 s")).toBeInTheDocument();
    const second = callItems(runs[1]);
    expect(second).toHaveLength(1);
    expect(within(second[0]).getByText("finish_screening")).toBeInTheDocument();
  });

  it("shows each stored argument as its name and canonical value, a null as (not stored), and no argument text for a call without any", async () => {
    mockWithTrace([
      toolRun({
        calls: [
          toolCall({ args: { field: "zone", count: 3, verified: true, codes: ["morning", "evening"], name: null } }),
          toolCall({ tool: "send_choice", args: {} }),
        ],
      }),
    ]);
    renderRoute(`/applications/${ID}`);
    const [run] = await runItems(await traceSection(await panel()));
    const [withArgs, without] = callItems(run);
    expect(argOf(withArgs, "field")).toBe("zone");
    expect(argOf(withArgs, "count")).toBe("3");
    expect(argOf(withArgs, "verified")).toBe("true");
    expect(argOf(withArgs, "codes")).toBe("morning, evening");
    expect(argOf(withArgs, "name")).toBe("(not stored)");
    expect(within(withArgs).getAllByText("(not stored)")).toHaveLength(1);
    expect(without.querySelector("dl")).toBeNull();
    expect(without).not.toHaveTextContent("(not stored)");
    expect(without.textContent).toBe(without.querySelector(".op-call__head")?.textContent);
  });

  it("reads ok, invalid, knockout and error each as its own text in a Tag, with the error code only where one is stored", async () => {
    mockWithTrace([
      toolRun({
        calls: [
          toolCall({ outcome: "ok" }),
          toolCall({ outcome: "invalid", error: "invalid_value" }),
          toolCall({ outcome: "knockout" }),
          toolCall({ outcome: "error", error: "internal" }),
        ],
      }),
    ]);
    renderRoute(`/applications/${ID}`);
    const [run] = await runItems(await traceSection(await panel()));
    const calls = callItems(run);
    expect(calls).toHaveLength(4);
    const tag = (li: HTMLElement) => li.querySelector(".ui-tag") as HTMLElement;
    expect(tag(calls[0])).toHaveTextContent(/^OK$/);
    expect(tag(calls[1])).toHaveTextContent(/^Invalid$/);
    expect(tag(calls[2])).toHaveTextContent(/^Knockout$/);
    expect(tag(calls[3])).toHaveTextContent(/^Error$/);
    expect(within(calls[1]).getByText("invalid_value")).toBeInTheDocument();
    expect(within(calls[3]).getByText("internal")).toBeInTheDocument();
    for (const i of [0, 2]) {
      expect(calls[i].querySelector(".op-call__error")).toBeNull();
      expect(calls[i].textContent).not.toContain("null");
    }
  });

  it("labels the group with no run id Run not recorded, with no numbers and no jump, and dashes the numbers a run lacks", async () => {
    mockWithTrace([
      toolRun({ status: null, input_tokens: null, output_tokens: null, cost_usd: null, latency_ms: null, reply_message_ids: [], calls: [toolCall({ tool: "record_answer" })] }),
      toolRun({
        run_id: null,
        status: null,
        input_tokens: null,
        output_tokens: null,
        cost_usd: null,
        latency_ms: null,
        reply_message_ids: [],
        calls: [toolCall({ tool: "send_choice" }), toolCall({ tool: "finish_screening" })],
      }),
    ]);
    renderRoute(`/applications/${ID}`);
    const runs = await runItems(await traceSection(await panel()));
    expect(runs).toHaveLength(2);
    const [numbered, unlinked] = runs;
    for (const label of ["Tokens in -", "Tokens out -", "Cost -", "Latency -"]) expect(within(numbered).getByText(label)).toBeInTheDocument();
    expect(numbered.querySelector(".op-run__head .ui-tag")).toBeNull();
    expect(callItems(numbered)).toHaveLength(1);
    expect(within(unlinked).getByRole("heading", { name: "Run not recorded" })).toBeInTheDocument();
    expect(within(unlinked).queryByText(/Run null/)).not.toBeInTheDocument();
    expect(within(unlinked).queryByText(/^(Tokens|Cost|Latency)/)).not.toBeInTheDocument();
    expect(within(unlinked).queryByRole("button", { name: "Show reply in Transcript" })).not.toBeInTheDocument();
    expect(callItems(unlinked)).toHaveLength(2);
  });

  it("Show reply in Transcript marks the bubble of the run's first reply message, and is disabled without a reply or outside the Transcript", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: scrollIntoView, configurable: true, writable: true });
    try {
      mockWithTrace([
        toolRun({ run_id: "run_1", reply_message_ids: [9, 2], calls: [toolCall()] }),
        toolRun({ run_id: "run_2", reply_message_ids: [2], calls: [toolCall()] }),
        toolRun({ run_id: "run_3", reply_message_ids: [], calls: [toolCall()] }),
        toolRun({ run_id: "run_4", reply_message_ids: [999], calls: [toolCall()] }),
      ]);
      renderRoute(`/applications/${ID}`);
      const runs = await runItems(await traceSection(await panel()));
      const jump = (run: HTMLElement) => within(run).getByRole("button", { name: "Show reply in Transcript" });
      const log = screen.getByRole("log", { name: "Transcript" });
      const bubble = (id: number) => log.querySelector(`[data-message-id="${id}"]`) as HTMLElement;
      expect(bubble(9)).not.toBeNull();
      expect(log.querySelector("[data-selected]")).toBeNull();
      log.getBoundingClientRect = () => ({ top: 100, bottom: 600, left: 0, right: 0, width: 0, height: 500, x: 0, y: 100, toJSON: () => ({}) });
      bubble(9).getBoundingClientRect = () => ({ top: 340, bottom: 380, left: 0, right: 0, width: 0, height: 40, x: 0, y: 340, toJSON: () => ({}) });
      bubble(2).getBoundingClientRect = () => ({ top: 240, bottom: 280, left: 0, right: 0, width: 0, height: 40, x: 0, y: 240, toJSON: () => ({}) });
      log.scrollTop = 0;

      expect(jump(runs[0])).toBeEnabled();
      expect(jump(runs[2])).toBeDisabled();
      expect(jump(runs[3])).toBeDisabled();
      fireEvent.click(jump(runs[0]));
      await waitFor(() => expect(bubble(9)).toHaveAttribute("data-selected"));
      expect(log.querySelectorAll("[data-selected]")).toHaveLength(1);
      fireEvent.click(jump(runs[1]));
      await waitFor(() => expect(bubble(2)).toHaveAttribute("data-selected"));
      expect(bubble(9)).not.toHaveAttribute("data-selected");
      expect(log.querySelectorAll("[data-selected]")).toHaveLength(1);
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("shows Trace unavailable with Retry when the request fails, keeps the rest of the panel, and Retry loads the runs", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals, [...detail().events, runError]),
      // Fails too, so the readings' own Retry shows beside the trace's and an unscoped query for "Retry" still finds one button.
      [`GET /api/applications/:id/jev-readings`]: () => [500, { error: "internal", message: "boom" }],
      [TRACE_PATH]: () => (calls++ === 0 ? [500, { error: "internal", message: "boom" }] : toolTrace([toolRun({ calls: [toolCall()] })])),
    });
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    expect(await within(region).findByText("Scores unavailable")).toBeInTheDocument();
    const notice = await within(region).findByText("Trace unavailable");
    expect(notice).toHaveAttribute("role", "status");
    expect(within(region).queryByRole("alert")).not.toBeInTheDocument();
    expect(rows(region)).toHaveLength(5);
    const counts = within(within(region).getByRole("list", { name: "Signal counts" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent?.replace(/\s+/g, " ").trim());
    expect(counts).toEqual(["Frustrated 2", "Confused 3", "Wants a person 1", "Abusive 0"]);
    const errors = within(region).getByRole("heading", { name: "Agent run errors" }).closest("section") as HTMLElement;
    expect(within(errors).getAllByRole("listitem")).toHaveLength(1);
    expect(within(region).getByRole("heading", { name: "Cost (Operators only)" })).toBeInTheDocument();
    expect(within(region).getByText("$0.0031")).toBeInTheDocument();
    expect(within(region).queryByRole("list", { name: "Runs with their tool calls" })).not.toBeInTheDocument();

    const retry = within(region).getByRole("button", { name: "Retry the agent runs" });
    expect(retry).toHaveTextContent(/^Retry$/);
    expect(within(region).getAllByRole("button", { name: "Retry" })).toHaveLength(1);
    fireEvent.click(retry);
    expect(await runList(region)).toBeInTheDocument();
    expect(mock.find("GET", `/api/applications/${ID}/tool-calls`)).toHaveLength(2);
    expect(within(region).queryByText("Trace unavailable")).not.toBeInTheDocument();
  });

  it("says no tool call was recorded in one muted line, with no list, when there are no runs", async () => {
    mockWithTrace([]);
    renderRoute(`/applications/${ID}`);
    const section = await traceSection(await panel());
    const line = await within(section).findByText("No tool calls recorded on this Application.");
    expect(line).toHaveClass("op-panel__muted");
    expect(within(section).queryByRole("list")).not.toBeInTheDocument();
    expect(within(section).queryByText("Trace unavailable")).not.toBeInTheDocument();
  });

  it("makes no trace request for a Recruiter", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: withSignals(fiveSignals),
      [TRACE_PATH]: toolTrace([toolRun({ calls: [toolCall()] })]),
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Agent runs" })).not.toBeInTheDocument();
    expect(mock.calls.filter((c) => c.path.endsWith("/tool-calls"))).toHaveLength(0);
  });

  it("makes no further trace request once an Operator previews the Recruiter view", async () => {
    const mock = mockWithTrace([toolRun({ calls: [toolCall()] })]);
    renderRoute(`/applications/${ID}`);
    await panel();
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Agent runs" })).not.toBeInTheDocument();
    const before = mock.calls.filter((c) => c.path.endsWith("/tool-calls")).length;
    expect(before).toBeLessThanOrEqual(1);
    await new Promise((r) => setTimeout(r, 50));
    expect(mock.calls.filter((c) => c.path.endsWith("/tool-calls"))).toHaveLength(before);
  });

  it("shows no message text, and ignores reasoning, hint and result keys a call or run may carry", async () => {
    const plain = toolCall({ args: { field: "zone" } });
    const extras = { ...plain, reasoning: "SECRET reasoning text", hint: "SECRET hint text", result: "SECRET result text" } as unknown as ToolCall;
    const run = { ...toolRun({ calls: [extras, plain] }), notes: "SECRET run note", reasoning: "SECRET run reasoning" } as unknown as ToolCallRun;
    mockWithTrace([run]);
    renderRoute(`/applications/${ID}`);
    const region = await panel();
    const [item] = await runItems(await traceSection(region));
    const [withExtras, clean] = callItems(item);
    expect(withExtras.textContent).toBe(clean.textContent);
    expect(withExtras.querySelectorAll("dt")).toHaveLength(1);
    expect(region.textContent).not.toContain("SECRET");
    const log = screen.getByRole("log", { name: "Transcript" });
    for (const m of messages) {
      if (!m.body) continue;
      expect(within(log).getByText(m.body)).toBeInTheDocument();
      expect(region.textContent).not.toContain(m.body);
    }
  });
});
