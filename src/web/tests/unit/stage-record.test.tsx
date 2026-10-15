import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApplicationDetail, JevReadings, MessageLabel, ToolCallTrace } from "../../src/api/types.ts";
import { argsText, buildTurns } from "../../src/stage/agentActivity.ts";
import { AuthContext, type AuthValue } from "../../src/auth/context.ts";
import { RecordPane } from "../../src/stage/RecordPane.tsx";
import type { ApplicationLookup } from "../../src/stage/useApplicationLookup.ts";
import { detail, operatorMe, summary } from "./fixtures.ts";
import { mockApi } from "./helpers.tsx";

// The Demo stage's record pane (ADR 0075): the "View as" switch, the panel under the record and Approve.

class SilentEventSource {
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

const APP = summary();
const DETAIL = `/api/applications/${APP.id}`;
const VIEW_AS_KEY = "criba.stage.viewAs";

const LABEL: MessageLabel = {
  message_id: 1,
  stage: "greeting",
  at: "2026-10-09T10:00:00Z",
  language: "es",
  fired: ["confused"],
  intent: "answering",
  tone_hint: "simpler",
  language_set: null,
  language_reason: null,
};

const READINGS: JevReadings = {
  application_id: APP.id,
  thresholds: {
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
  },
  readings: [
    {
      message_id: 1,
      at: "2026-10-09T10:00:00Z",
      stage: "greeting",
      status: "ok",
      reading: {
        lang: "es",
        lang_confidence: 0.99,
        switch: 0.05,
        frustrated: 0.1,
        confused: 0.93,
        wants_human: 0.05,
        abusive: 0.02,
        fired: ["confused"],
        hint: "simpler",
        escalation: null,
        model: "typesafe/jev-1.13",
        policy_version: "jev-1.13-v3",
        first_message: true,
        language_set: "es-MX",
        intent: { choice: "steering", confidence: 0.88 },
        malice: 0.71,
      },
    },
  ],
};

const operator: AuthValue = {
  me: operatorMe,
  loading: false,
  error: null,
  refetch: () => undefined,
  realRole: "operator",
  role: "operator",
  isPreviewingRecruiter: false,
  setPreviewRecruiter: () => undefined,
  login: async () => operatorMe,
  logout: async () => undefined,
};

function mount(value: ApplicationDetail, extra: Parameters<typeof mockApi>[0] = {}) {
  const mock = mockApi({
    [`GET ${DETAIL}`]: () => value,
    "GET /api/applications/:id/jev-readings": READINGS,
    "GET /api/applications/:id/tool-calls": { application_id: APP.id, runs: [] },
    "GET /api/sim/clock": [404, { error: "not_found", message: "no" }],
    ...extra,
  });
  const lookup: ApplicationLookup = { application: APP, started: true, isError: false, error: null, retry: vi.fn() };
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })}>
      <AuthContext.Provider value={operator}>
        <RecordPane lookup={lookup} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
  return mock;
}

const under = () => screen.getByRole("region", { name: "Conversation readings" });
/** Opens the panel under the record (closed by default) with its toggle. */
const openUnder = () => fireEvent.click(within(under()).getByRole("button", { expanded: false }));
const record = () => screen.getByRole("region", { name: "Record" });

beforeEach(() => {
  vi.stubGlobal("EventSource", SilentEventSource);
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("stage record: View as", () => {
  it("defaults to Operator: the full Jev readings panel under the record, with scores and no economics", async () => {
    mount(detail({ message_labels: [LABEL] }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    const group = screen.getByRole("group", { name: "View as" });
    expect(within(group).getByRole("button", { name: "Operator" })).toHaveAttribute("aria-pressed", "true");
    openUnder();
    const jevTab = within(under()).getByRole("tab", { name: "Jev readings" });
    within(under()).getByRole("tab", { name: "Agent activity" }).focus();
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowRight" });
    await waitFor(() => expect(jevTab).toHaveAttribute("aria-selected", "true"));
    const list = await within(under()).findByRole("list", { name: "Jev readings by message" });
    expect(within(list).getAllByRole("meter").length).toBeGreaterThan(0);
    expect(list).toHaveTextContent("0.93");
    expect(list).toHaveTextContent("Manipulation");
    // No jump to a Transcript the stage does not have, and no cost card.
    expect(within(under()).queryByRole("button", { name: "Show in Transcript" })).not.toBeInTheDocument();
    expect(under()).not.toHaveTextContent(/\$\d/);
    expect(within(record()).getByRole("tablist", { name: "Record views" })).toBeInTheDocument();
  });

  it("shows the Recruiter's record and the Message labels in Recruiter mode: no scores, no Operator panels", async () => {
    const mock = mount(detail({ message_labels: [LABEL] }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Recruiter" }));
    expect(screen.getByRole("button", { name: "Recruiter" })).toHaveAttribute("aria-pressed", "true");
    openUnder();
    const labels = within(under()).getByRole("list", { name: "Message labels" });
    expect(labels).toHaveTextContent("Confused");
    expect(labels).toHaveTextContent("Intent: Answering");
    expect(labels).toHaveTextContent("Tone hint: simpler words");
    expect(within(labels).getByTestId("message-labels").textContent).not.toMatch(/\d/);
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.queryByText(/Manipulation|Agent runs|Operator panel|Steering/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("0.93");
    // The record is the Recruiter's view alone: no Agent's view to switch to.
    expect(within(record()).queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByTestId("record-recruiter")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(VIEW_AS_KEY)).toBe("recruiter");
    // A presentation switch: the server is asked nothing new for it.
    expect(mock.calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("remembers the choice per tab, and works when storage throws", async () => {
    window.sessionStorage.setItem(VIEW_AS_KEY, "recruiter");
    mount(detail({ message_labels: [LABEL] }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    expect(screen.getByRole("button", { name: "Recruiter" })).toHaveAttribute("aria-pressed", "true");
    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mount(detail({ message_labels: [LABEL] }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    expect(screen.getByRole("button", { name: "Operator" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Recruiter" }));
    expect(screen.getByRole("button", { name: "Recruiter" })).toHaveAttribute("aria-pressed", "true");
  });
});

// Agent activity (ADR 0075): every line from a stored record, one block per Candidate turn, Operator view only.
const TRACE: ToolCallTrace = {
  application_id: APP.id,
  runs: [
    {
      run_id: "run_a",
      status: "ok",
      input_tokens: 7200,
      output_tokens: 410,
      cost_usd: 0.0031,
      latency_ms: 1700,
      at: "2026-10-09T10:00:01Z",
      hermes_profile: "criba-recruiter",
      model: "anthropic/claude-haiku-5.5",
      reply_message_ids: [2],
      calls: [
        { tool: "get_application", started_at: "2026-10-09T10:00:01Z", duration_ms: 12, outcome: "ok", error: null, args: {} },
        { tool: "record_answer", started_at: "2026-10-09T10:00:01Z", duration_ms: 31, outcome: "invalid", error: "invalid_value", args: { field: "zone", value: null } },
        { tool: "record_answer", started_at: "2026-10-09T10:00:01Z", duration_ms: 28, outcome: "ok", error: null, args: { field: "zone", value: "zone_mx_puebla_centro" } },
        { tool: "record_answer", started_at: "2026-10-09T10:00:02Z", duration_ms: 20, outcome: "knockout", error: null, args: { field: "driver_license", value: false } },
      ],
    },
  ],
  jev_calls: [{ at: "2026-10-09T10:00:00Z", model: "typesafe/jev-1.13", latency_ms: 312, status: "ok" }],
};

const activityDetail = () =>
  detail({
    events: [
      { id: 1, kind: "application_started", actor: "candidate", user_name: null, data: {}, created_at: "2026-10-09T10:00:00Z" },
      { id: 2, kind: "answer_recorded", actor: "agent", user_name: null, data: { field: "zone", value: "zone_mx_puebla_centro" }, created_at: "2026-10-09T10:00:01Z" },
      { id: 3, kind: "stage_changed", actor: "agent", user_name: null, data: { from: "zone", to: "driver_license" }, created_at: "2026-10-09T10:00:01Z" },
    ],
  });

describe("stage record: Agent activity", () => {
  it("shows the legend and a turn: message in, Jev, the run, each tool call with what code decided, code's events, reply out", async () => {
    mount(activityDetail(), { "GET /api/applications/:id/tool-calls": TRACE });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    openUnder();
    const tabs = within(under()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Agent activity", "Jev readings"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    const activity = within(under()).getByTestId("agent-activity");
    expect(activity).toHaveTextContent("Candidate → our backend (channel and router) → Jev");
    const turns = await within(activity).findByRole("list", { name: "Turns" });
    await waitFor(() => expect(turns).toHaveTextContent("criba-recruiter"));
    await waitFor(() => expect(turns).toHaveTextContent("Spanish 0.99"));
    const steps = Array.from(turns.querySelectorAll<HTMLElement>("[data-step]")).map((el) => el.dataset.step);
    expect(steps.slice(0, 7)).toEqual(["in", "jev", "run", "tool", "tool", "tool", "tool"]);
    expect(turns).toHaveTextContent("Candidate message in · typed");
    expect(turns).toHaveTextContent("312 ms · typesafe/jev-1.13");
    expect(turns).toHaveTextContent("anthropic/claude-haiku-5.5 · 7,200 in / 410 out · 1.7 s · $0.0031 · ok");
    expect(turns).toHaveTextContent("record_answer(field=zone, value=-) → rejected by code: invalid_value");
    expect(turns).toHaveTextContent("record_answer(field=zone, value=zone_mx_puebla_centro) → accepted by code");
    expect(turns).toHaveTextContent("record_answer(field=driver_license, value=false) → Knockout decided by code");
    expect(turns).toHaveTextContent("Answer recorded: Zone = Centro");
    expect(turns).toHaveTextContent("Stage changed → Driver's license");
    expect(turns).toHaveTextContent("Reply out");
    expect(turns).toHaveTextContent("with 2 reply options");
    // No Candidate words anywhere in the timeline.
    expect(turns).not.toHaveTextContent("Hola, quiero trabajar");
    expect(turns).not.toHaveTextContent("Vivo en Centro");
  });

  it("is hidden in View as Recruiter", async () => {
    mount(activityDetail(), { "GET /api/applications/:id/tool-calls": TRACE });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Recruiter" }));
    openUnder();
    expect(screen.queryByTestId("agent-activity")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Agent activity" })).not.toBeInTheDocument();
    expect(screen.queryByText(/criba-recruiter|record_answer|claude/)).not.toBeInTheDocument();
  });

  it("puts each record in the turn whose window holds it, and leaves an empty lead turn out", () => {
    const base = activityDetail();
    const second = { ...base.messages[0], id: 50, created_at: "2026-10-09T10:02:00Z", content_type: "button_reply" as const };
    const turns = buildTurns({
      messages: [...base.messages, second],
      events: base.events,
      runs: [...TRACE.runs, { ...TRACE.runs[0], run_id: "run_b", at: "2026-10-09T10:02:01Z", calls: [] }],
      jevCalls: TRACE.jev_calls ?? [],
      readings: [],
    });
    expect(turns.map((t) => t.inbound?.id)).toEqual([1, 50]);
    expect(turns[0].runs.map((r) => r.run_id)).toEqual(["run_a"]);
    expect(turns[1].runs.map((r) => r.run_id)).toEqual(["run_b"]);
    expect(turns[0].jevCalls).toHaveLength(1);
    // application_started is not one of code's follow-ups; answer_recorded and stage_changed are.
    expect(turns[0].events.map((e) => e.kind)).toEqual(["answer_recorded", "stage_changed"]);
    expect(turns[0].replies.map((m) => m.id)).toEqual([2, 9]);
  });

  it("writes arguments as canonical name=value pairs, and a dash for anything that is not a code", () => {
    expect(argsText({ field: "zone", value: "zone_x", ok: true, n: 3, list: ["a", "b"], note: null, obj: { text: "hola" } })).toBe(
      "field=zone, value=zone_x, ok=true, n=3, list=[a, b], note=-, obj=-",
    );
  });
});

describe("stage record: the panel under the record is closed by default", () => {
  it("shows a one-line toggle and nothing else, fetches nothing for it, and opens and closes in one click", async () => {
    const mock = mount(activityDetail(), { "GET /api/applications/:id/tool-calls": TRACE });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    const toggle = within(under()).getByRole("button", { name: "Agent activity and Jev readings" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(under()).toHaveAttribute("data-open", "false");
    // Closed: not a scroll region, so not a tab stop of its own.
    expect(under()).not.toHaveAttribute("tabindex");
    expect(within(under()).queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-activity")).not.toBeInTheDocument();
    expect(mock.calls.filter((c) => /\/(tool-calls|jev-readings)$/.test(c.path))).toHaveLength(0);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(under()).toHaveAttribute("tabindex", "0");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).not.toHaveAttribute("hidden");
    expect(within(under()).getByRole("tab", { name: "Agent activity" })).toHaveAttribute("aria-selected", "true");
    expect(within(under()).getByTestId("agent-activity")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("agent-activity")).not.toBeInTheDocument();
  });

  it("names the Message labels on its toggle in Recruiter view, still closed", async () => {
    mount(detail({ message_labels: [LABEL] }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Recruiter" }));
    const toggle = within(under()).getByRole("button", { name: "Message labels" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(under()).queryByRole("list", { name: "Message labels" })).not.toBeInTheDocument();
  });
});

describe("stage record: no Operator, nothing under the record", () => {
  it("renders the record alone when the effective role is not operator", async () => {
    mockApi({ [`GET ${DETAIL}`]: () => detail({ message_labels: [LABEL] }) });
    const lookup: ApplicationLookup = { application: APP, started: true, isError: false, error: null, retry: vi.fn() };
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
        <AuthContext.Provider value={{ ...operator, role: "recruiter", isPreviewingRecruiter: true }}>
          <RecordPane lookup={lookup} />
        </AuthContext.Provider>
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "Luis Hernández" });
    expect(screen.queryByRole("region", { name: "Conversation readings" })).not.toBeInTheDocument();
  });
});

describe("stage record: Approve", () => {
  it("shows Approve for a Qualified Application with an open Approval and calls the Review queue's endpoint", async () => {
    const mock = mount(detail(), { "POST /api/queue/approvals/:id": { id: "qi_approval_1", kind: "approval", status: "resolved" } });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")[0].body).toEqual({ decision: "approved" });
    expect(await screen.findByRole("status")).toHaveTextContent("Approved.");
    // The record is read again after the decision.
    await waitFor(() => expect(mock.find("GET", DETAIL).length).toBeGreaterThan(1));
  });

  it("shows Approve in Recruiter mode too: it is the logged-in Operator's decision either way", async () => {
    mount(detail());
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Recruiter" }));
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("shows no Approve without an open Approval, before Qualified or on a closed Application", async () => {
    for (const value of [
      detail({ open_queue_items: [] }),
      detail({ stage: "start_date" }),
      detail({ status: "closed" }),
      detail({ open_queue_items: [{ id: "qi_e", kind: "escalation", status: "open", reason: "wants_human", note: null, created_at: "2026-10-09T10:04:00Z" }] }),
    ]) {
      mount(value);
      await screen.findByRole("heading", { name: "Luis Hernández" });
      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
      cleanup();
    }
  });

  it("says why when the approval fails", async () => {
    mount(detail(), { "POST /api/queue/approvals/:id": [409, { error: "conflict", message: "Already resolved." }] });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not approve. Already resolved.");
  });
});
