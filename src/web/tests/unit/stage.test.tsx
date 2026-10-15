/// <reference types="node" />
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import type { ApplicationDetail, ApplicationSummary, ChatMessage } from "../../src/api/types.ts";
import { useConversation, type Conversation } from "../../src/api/useConversation.ts";
import { AuthContext, type AuthValue } from "../../src/auth/context.ts";
import { ClockStrip } from "../../src/stage/ClockStrip.tsx";
import { bubbleTime, countryOfPhone, formatZoned, generatePhone, initials, isValidPhone, matchByPhone, tickState } from "../../src/stage/phone.ts";
import { PhonePane } from "../../src/stage/PhonePane.tsx";
import { RecordPane, type StageDetail } from "../../src/stage/RecordPane.tsx";
import { useApplicationLookup, type ApplicationLookup } from "../../src/stage/useApplicationLookup.ts";
import { StageGate } from "../../src/stage/StageGate.tsx";
import { CONSOLE_SHORTCUT, matchesShortcut, PLAYBACK_SHORTCUT } from "../../src/stage/PresenterConsole.tsx";
import { RECORDING_WINDOW, recordingWindow } from "../../src/stage/recordingWindow.ts";
import { PERSONAS } from "../../src/stage/playback.ts";
import { MAX_TAKES, TAKES_KEY, type Take } from "../../src/stage/takes.ts";
import { useStageSession } from "../../src/stage/useStageSession.ts";
import { claimByAna, clockState, detail, operatorMe, recruiterMe, summary } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// The panes are a lazy chunk behind the gate. This counts how often the module's factory runs, which is
// once, on the first import of StageScreen.tsx. The gate tests come first in this file and assert 0.
const screenLoads = vi.hoisted(() => ({ count: 0 }));
vi.mock("../../src/stage/StageScreen.tsx", async (importOriginal) => {
  screenLoads.count += 1;
  return importOriginal();
});

// The console host is its own lazy chunk, and this file also imports the shortcut constants from the same
// module, so counting factory runs would count that import. `loads` counts the gate's ask for the host by
// name: React.lazy asks once per module, so it is only meaningful before any test has rendered the stage,
// which is where the gate tests below read it. `renders` counts mounts of the host, per test.
const consoleLoads = vi.hoisted(() => ({ loads: 0, renders: 0 }));
vi.mock("../../src/stage/PresenterConsole.tsx", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../src/stage/PresenterConsole.tsx")>();
  const Counted = () => {
    consoleLoads.renders += 1;
    return original.StageConsole();
  };
  return {
    ...original,
    get StageConsole() {
      consoleLoads.loads += 1;
      return Counted;
    },
  };
});

const CONV = "wc_9f2c7e1b4a6d4e0f8b3a5c7d9e1f2a3b";
const STORAGE = "criba.testChat.conversation";

function msg(partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "direction" | "sender" | "text">): ChatMessage {
  return {
    message_id: null,
    sender_label: null,
    interactive: null,
    reply_to: null,
    links: [],
    attachments: [],
    created_at: "2026-10-09T10:00:10Z",
    ...partial,
  };
}

const hello = msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola, soy Ana" });
const question = msg({
  id: 2,
  direction: "outbound",
  sender: "agent",
  sender_label: "Sazón Empleo",
  text: "¿Tienes licencia?",
  interactive: { style: "buttons", options: [{ id: "yes", label: "Sí" }, { id: "no", label: "No" }] },
});

// ---------------------------------------------------------------------------------------------
// Shared chat hook (SP3)
// ---------------------------------------------------------------------------------------------

/** Renders the hook's values as text and hands the whole value to the test after every render. */
function Harness(props: { storageKey: string | null; onValue: (value: Conversation) => void }) {
  const value = useConversation({ storageKey: props.storageKey });
  const { onValue } = props;
  useEffect(() => {
    onValue(value);
  });
  return (
    <div>
      <p data-testid="id">{value.conversationId ?? "none"}</p>
      <p data-testid="ids">{value.messages.map((m) => m.id).join(",")}</p>
      <p data-testid="run">{String(value.runFinished)}</p>
      <p data-testid="sent">{value.sent}</p>
      <p data-testid="answerable">{String(value.answerableId)}</p>
      <p data-testid="error">{value.error ?? ""}</p>
    </div>
  );
}

function mountHook(storageKey: string | null = null) {
  const seen: { current: Conversation | null } = { current: null };
  const view = render(<Harness storageKey={storageKey} onValue={(v) => {
        seen.current = v;
      }} />);
  return {
    ...view,
    now: () => seen.current as Conversation,
    text: (id: string) => screen.getByTestId(id).textContent,
  };
}

const READ = `/api/chat/conversations/${CONV}/messages`;

describe("useConversation", () => {
  it("resumes a conversation by reading from cursor 0", async () => {
    const mock = mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({
        cursor: 2,
        agent_typing: false,
        run_finished: true,
        persona_name: "Sazón Empleo",
        language: "es-MX",
        messages: [hello, question],
      }),
    });
    const hook = mountHook();
    expect(hook.text("id")).toBe("none");
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("1,2"));
    expect(hook.text("id")).toBe(CONV);
    expect(mock.find("GET", READ)[0].query.get("after")).toBe("0");
    expect(mock.find("GET", READ)[0].query.get("wait")).toBe("20");
    expect(hook.now().personaName).toBe("Sazón Empleo");
    expect(hook.now().language).toBe("es-MX");
  });

  it("reads again from cursor 0 when resume is called for the conversation that is already open", async () => {
    const mock = mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 1, agent_typing: false, run_finished: true, messages: [hello] }),
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("1"));
    act(() => hook.now().resume(CONV));
    const fromStart = () => mock.find("GET", READ).filter((c) => c.query.get("after") === "0");
    await waitFor(() => expect(fromStart().length).toBeGreaterThanOrEqual(2));
    expect(hook.text("ids")).toBe("1");
  });

  it("follows run_finished from the page: null before the first page, then the page's value", async () => {
    let finished = false;
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 1, agent_typing: false, run_finished: finished, messages: [hello] }),
      "POST /api/chat/conversations/:id/messages": [202, { message_id: "x", id: 3, duplicate: false }],
    });
    const hook = mountHook();
    expect(hook.now().runFinished).toBeNull();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("run")).toBe("false"));
    expect(hook.now().typing).toBe(true);
    finished = true;
    // A send wakes the poll at once instead of waiting for the next long poll to end.
    await act(async () => {
      await hook.now().send("otra");
    });
    await waitFor(() => expect(hook.text("run")).toBe("true"));
    expect(hook.now().typing).toBe(false);
  });

  it("counts one accepted send and one accepted tap in sent", async () => {
    const store: ChatMessage[] = [question];
    const mock = mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 2, agent_typing: false, run_finished: true, messages: store }),
      "POST /api/chat/conversations/:id/messages": [202, { message_id: "m", id: 3, duplicate: false }],
      "POST /api/chat/conversations/:id/replies": [202, { message_id: "r", id: 4, duplicate: false }],
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("2"));
    expect(hook.text("sent")).toBe("0");

    let sent = false;
    await act(async () => {
      sent = await hook.now().send("Hola");
    });
    expect(sent).toBe(true);
    expect(hook.text("sent")).toBe("1");
    const body = mock.find("POST", READ)[0].body as { message_id: string; text: string };
    expect(body.text).toBe("Hola");
    expect(body.message_id.length).toBeGreaterThan(8);

    let tapped = false;
    await act(async () => {
      tapped = await hook.now().tap(question, "yes");
    });
    expect(tapped).toBe(true);
    expect(hook.text("sent")).toBe("2");
    expect(mock.find("POST", `/api/chat/conversations/${CONV}/replies`)[0].body).toMatchObject({ in_reply_to: 2, option_id: "yes" });
  });

  it("clears the id and says so when a read answers 404", async () => {
    mockApi({ "GET /api/chat/conversations/:id/messages": error(404, "not_found", "Conversation not found.") });
    window.sessionStorage.setItem(STORAGE, CONV);
    const hook = mountHook(STORAGE);
    expect(hook.text("id")).toBe(CONV);
    await waitFor(() => expect(hook.text("error")).toBe("That conversation no longer exists. Start a new one."));
    expect(hook.text("id")).toBe("none");
    expect(window.sessionStorage.getItem(STORAGE)).toBeNull();
  });

  it("returns false and sets error when a send fails", async () => {
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
      "POST /api/chat/conversations/:id/messages": error(500, "internal", "Could not store the message."),
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("run")).toBe("true"));
    let sent = true;
    await act(async () => {
      sent = await hook.now().send("Hola");
    });
    expect(sent).toBe(false);
    expect(hook.text("error")).toBe("Could not store the message.");
    expect(hook.text("sent")).toBe("0");
  });

  it("returns false and sets error when a tap fails", async () => {
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 2, agent_typing: false, run_finished: true, messages: [question] }),
      "POST /api/chat/conversations/:id/replies": error(409, "option_expired", "Esas opciones ya no están disponibles."),
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("2"));
    let tapped = true;
    await act(async () => {
      tapped = await hook.now().tap(question, "yes");
    });
    expect(tapped).toBe(false);
    expect(hook.text("error")).toBe("Esas opciones ya no están disponibles.");
    expect(hook.text("sent")).toBe("0");
  });

  it("adds no duplicate when a page repeats ids it has already seen", async () => {
    mockApi({
      // The page ignores `after`, like a server that echoes the whole thread every time.
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 2, agent_typing: false, run_finished: true, messages: [hello, question] }),
      "POST /api/chat/conversations/:id/messages": [202, { message_id: "m", id: 3, duplicate: false }],
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("1,2"));
    await act(async () => {
      await hook.now().send("otra vez");
    });
    await waitFor(() => expect(hook.now().busy).toBe(false));
    expect(hook.text("ids")).toBe("1,2");
  });

  it("makes the latest interactive message answerable until a later inbound message", async () => {
    let store: ChatMessage[] = [hello, question];
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: store.at(-1)?.id ?? 0, agent_typing: false, run_finished: true, messages: store }),
      "POST /api/chat/conversations/:id/messages": () => {
        store = [...store, msg({ id: 3, direction: "inbound", sender: "candidate", text: "Sí tengo" })];
        return [202, { message_id: "m", id: 3, duplicate: false }];
      },
    });
    const hook = mountHook();
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("answerable")).toBe("2"));
    await act(async () => {
      await hook.now().send("Sí tengo");
    });
    await waitFor(() => expect(hook.text("ids")).toBe("1,2,3"));
    expect(hook.text("answerable")).toBe("null");
  });

  it("starts a conversation with the request it is given and shows the persona's name", async () => {
    const mock = mockApi({
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo", language: "es-MX" }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
    });
    const hook = mountHook();
    let started = false;
    await act(async () => {
      started = await hook.now().start({ country: "MX", locale: "es-MX", phone: "+525512345678", client: "demo" });
    });
    expect(started).toBe(true);
    expect(mock.find("POST", "/api/chat/conversations")[0].body).toEqual({ country: "MX", locale: "es-MX", phone: "+525512345678", client: "demo" });
    expect(hook.text("id")).toBe(CONV);
    expect(hook.now().personaName).toBe("Sazón Empleo");
    await waitFor(() => expect(hook.text("run")).toBe("true"));
  });

  it("returns false and sets error when the start fails", async () => {
    mockApi({ "POST /api/chat/conversations": error(422, "invalid", "Invalid phone.") });
    const hook = mountHook();
    let started = true;
    await act(async () => {
      started = await hook.now().start({ country: "MX", locale: "es-MX", phone: "+52", client: "demo" });
    });
    expect(started).toBe(false);
    expect(hook.text("error")).toBe("Invalid phone.");
    expect(hook.text("id")).toBe("none");
    expect(hook.now().busy).toBe(false);
  });

  it("reset forgets the conversation and its messages", async () => {
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 1, agent_typing: false, run_finished: true, messages: [hello] }),
    });
    const hook = mountHook(STORAGE);
    act(() => hook.now().resume(CONV));
    await waitFor(() => expect(hook.text("ids")).toBe("1"));
    expect(window.sessionStorage.getItem(STORAGE)).toBe(CONV);
    act(() => hook.now().reset());
    expect(hook.text("id")).toBe("none");
    expect(hook.text("ids")).toBe("");
    expect(hook.text("run")).toBe("null");
    expect(window.sessionStorage.getItem(STORAGE)).toBeNull();
  });

  it("never touches sessionStorage when storageKey is null", async () => {
    const get = vi.spyOn(Storage.prototype, "getItem");
    const set = vi.spyOn(Storage.prototype, "setItem");
    const remove = vi.spyOn(Storage.prototype, "removeItem");
    try {
      mockApi({
        "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
        "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
      });
      const hook = mountHook(null);
      await act(async () => {
        await hook.now().start({ country: "MX", locale: "es-MX", phone: "+525512345678", client: "demo" });
      });
      await waitFor(() => expect(hook.text("run")).toBe("true"));
      act(() => hook.now().reset());
      act(() => hook.now().resume(CONV));
      expect(get).not.toHaveBeenCalled();
      expect(set).not.toHaveBeenCalled();
      expect(remove).not.toHaveBeenCalled();
    } finally {
      get.mockRestore();
      set.mockRestore();
      remove.mockRestore();
    }
  });
});

// ---------------------------------------------------------------------------------------------
// Gate (SP1). These come before every test that opens the stage as an Operator, so the factory above
// can only have run if a failed gate loaded the panes.
// ---------------------------------------------------------------------------------------------

const previewOn = () => window.sessionStorage.setItem("criba.previewRecruiter", "1");

function authValue(over: Partial<AuthValue>): AuthValue {
  return {
    me: null,
    loading: false,
    error: null,
    refetch: () => undefined,
    realRole: null,
    role: null,
    isPreviewingRecruiter: false,
    setPreviewRecruiter: () => undefined,
    login: async () => operatorMe,
    logout: async () => undefined,
    ...over,
  };
}

function renderGate(value: AuthValue) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthContext.Provider value={value}>
          <StageGate />
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("stage gate", () => {
  it("shows a Recruiter Page not found and never loads the panes", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Phone" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Demo stage" })).not.toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("shows an Operator under Preview as Recruiter Page not found and never loads the panes", async () => {
    previewOn();
    const mock = mockApi({ "GET /api/auth/me": operatorMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
    expect(mock.find("GET", "/api/sim/clock")).toHaveLength(0);
  });

  it("shows the page's skeleton, not Page not found, while /api/auth/me is pending", async () => {
    const mock = mockApi({ "GET /api/auth/me": () => new Promise<object>(() => undefined) });
    renderRoute("/stage");
    // The lazy route's fallback and RequireAuth's own skeleton are separate elements: wait until the
    // question has been asked, so the one on screen is RequireAuth's.
    await waitFor(() => expect(mock.find("GET", "/api/auth/me")).toHaveLength(1));
    expect(await screen.findByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Phone" })).not.toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("shows the gate's own skeleton while auth is loading, never Page not found and never the stage", () => {
    renderGate(authValue({ loading: true }));
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Phone" })).not.toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("shows a logged-out visitor Page not found when the gate is reached directly", () => {
    renderGate(authValue({ me: null, realRole: null }));
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("gates on the real role: a Recruiter and an Operator in preview both get Page not found directly", () => {
    const { unmount } = renderGate(authValue({ me: recruiterMe, realRole: "recruiter", role: "recruiter" }));
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    unmount();
    renderGate(authValue({ me: operatorMe, realRole: "operator", role: "recruiter", isPreviewingRecruiter: true }));
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("sends a logged-out visitor to the login page through the route table", async () => {
    mockApi({ "GET /api/auth/me": error(401, "unauthorized", "Log in to continue.") });
    const { router } = renderRoute("/stage");
    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });

  it("has no link to /stage on the Applications and Overview pages", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/applications": { items: [], next_cursor: null, total: 0 },
      "GET /api/locations": { items: [] },
    });
    const first = renderRoute("/applications");
    await screen.findByRole("navigation", { name: "Main" });
    await screen.findByRole("heading", { name: "Applications" });
    expect(document.querySelectorAll('a[href$="/stage"]')).toHaveLength(0);
    first.unmount();

    mockApi({ "GET /api/auth/me": operatorMe });
    renderRoute("/overview");
    await screen.findByRole("navigation", { name: "Main" });
    await screen.findByRole("heading", { level: 1 });
    expect(document.querySelectorAll('a[href$="/stage"]')).toHaveLength(0);
    expect(screenLoads.count).toBe(0);
    expect(consoleLoads.loads).toBe(0);
  });
});

describe("stage screen", () => {
  it("opens for an Operator: the Phone and Record regions and one visually hidden Demo stage heading", async () => {
    const mock = mockApi({ "GET /api/auth/me": operatorMe });
    renderRoute("/stage");
    expect(await screen.findByRole("region", { name: "Phone" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Record" })).toBeInTheDocument();
    // findByRole throws when two level-1 headings match; the shell's tests rely on there being one.
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Demo stage");
    expect(heading).toHaveClass("ui-sr-only");
    expect(heading).not.toHaveAttribute("hidden");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
    expect(screenLoads.count).toBe(1);
    expect(mock.find("GET", "/api/applications")).toHaveLength(0);
  });

  it("wires the phone to the record: after the first message, the Application of the Take's phone appears, without cost", async () => {
    const thread: ChatMessage[] = [];
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo" }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: thread.at(-1)?.id ?? 0, agent_typing: false, run_finished: true, messages: thread }),
      "POST /api/chat/conversations/:id/messages": (call) => {
        thread.push(msg({ id: 1, direction: "inbound", sender: "candidate", text: (call.body as { text: string }).text }));
        return [202, { message_id: "m", id: 1, duplicate: false }];
      },
      "GET /api/applications": (call) => {
        const phone = call.query.get("q") as string;
        const base = summary();
        return { items: [{ ...base, candidate: { ...base.candidate, phone } }], next_cursor: null, total: 1 };
      },
      [`GET ${DETAIL_PATH}`]: () => detail(),
    });
    renderRoute("/stage");
    const region = await screen.findByRole("region", { name: "Record" });
    expect(region).toHaveTextContent("The Application appears here after the Candidate's first message");
    expect(mock.find("GET", "/api/applications")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "Hola, soy Luis" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await within(region).findByRole("heading", { name: "Luis Hernández" })).toBeInTheDocument();
    const start = mock.find("POST", "/api/chat/conversations")[0].body as { phone: string; client: string };
    expect(start.client).toBe("demo");
    expect(mock.find("GET", "/api/applications")[0].query.get("q")).toBe(start.phone);
    expect(document.body.textContent).not.toContain("0.0031");
  });

  it("renders its console slot only when something is passed, and nothing by default", async () => {
    const { StageScreen } = await import("../../src/stage/StageScreen.tsx");
    mockApi({ "GET /api/sim/clock": error(404, "sim_disabled", "No simulated clock.") });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const wrap = (children: ReactNode) => (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
    const view = render(wrap(<StageScreen />));
    expect(screen.queryByTestId("stage-console")).not.toBeInTheDocument();
    view.rerender(wrap(<StageScreen console={<p>Console content</p>} />));
    expect(within(screen.getByTestId("stage-console")).getByText("Console content")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------
// Clock strip (SP2)
// ---------------------------------------------------------------------------------------------

describe("clock strip", () => {
  const clock = (reply: unknown) => ({ "GET /api/auth/me": operatorMe, "GET /api/sim/clock": reply as object });
  const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 60)));

  it("shows the region with the time when the clock answers 200 with a parseable now", async () => {
    mockApi(clock(clockState));
    renderRoute("/stage");
    const region = await screen.findByRole("region", { name: "Simulated clock" });
    // On the stage the clock reads in the Take's zone (Mexico City by default), like the bubbles.
    expect(region).toHaveTextContent("04:00");
    expect(region).toHaveTextContent("Mexico City");
    expect(region.querySelector("time")).toHaveAttribute("datetime", new Date(clockState.now).toISOString());
  });

  it.each([
    ["a 404", error(404, "sim_disabled", "No simulated clock.")],
    ["a 500", error(500, "internal", "boom")],
    ["a rejected fetch", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a 200 with an HTML body", new Response("<html><body>proxy</body></html>", { status: 200 })],
    ["a 200 with an empty object", {}],
    ["a 200 with a now that is not a date", { now: "soon" }],
    ["no answer yet", () => new Promise<object>(() => undefined)],
  ])("renders no region for %s, but still the stage", async (_name, reply) => {
    const mock = mockApi(clock(reply));
    renderRoute("/stage");
    await screen.findByRole("region", { name: "Phone" });
    await waitFor(() => expect(mock.find("GET", "/api/sim/clock")).toHaveLength(1));
    await settle();
    expect(screen.queryByRole("region", { name: "Simulated clock" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Demo stage");
  });

  it("names the zone once and shows the time in it when the stage gives its country", async () => {
    mockApi({ "GET /api/sim/clock": clockState });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const view = render(
      <QueryClientProvider client={client}>
        <ClockStrip country="MX" />
      </QueryClientProvider>,
    );
    const region = await screen.findByRole("region", { name: "Simulated clock" });
    expect(region).toHaveTextContent("04:00");
    expect(region).toHaveTextContent("Mexico City");
    expect(region).not.toHaveTextContent("UTC");
    view.rerender(
      <QueryClientProvider client={client}>
        <ClockStrip country="ES" />
      </QueryClientProvider>,
    );
    expect(region).toHaveTextContent("12:00");
    expect(region).toHaveTextContent("Madrid");
  });

  it("asks the clock once per mount and never advances or resets it", async () => {
    const mock = mockApi(clock(clockState));
    renderRoute("/stage");
    await screen.findByRole("region", { name: "Simulated clock" });
    await settle();
    expect(mock.find("GET", "/api/sim/clock")).toHaveLength(1);
    expect(mock.calls.filter((c) => c.method === "POST" && c.path.startsWith("/api/sim/"))).toHaveLength(0);
  });

  it("renders its children inside the region, which is where the playback controls mount", async () => {
    mockApi({ "GET /api/sim/clock": clockState });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <ClockStrip>
          <button type="button">Advance</button>
        </ClockStrip>
      </QueryClientProvider>,
    );
    const region = await screen.findByRole("region", { name: "Simulated clock" });
    expect(within(region).getByRole("button", { name: "Advance" })).toBeInTheDocument();
  });

  it("renders no children either when the clock is unavailable", async () => {
    const mock = mockApi({ "GET /api/sim/clock": error(404, "sim_disabled", "No simulated clock.") });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <ClockStrip>
          <button type="button">Advance</button>
        </ClockStrip>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mock.find("GET", "/api/sim/clock")).toHaveLength(1));
    await settle();
    expect(screen.queryByRole("button", { name: "Advance" })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------
// Phone helpers (SP4, SP7, SP8)
// ---------------------------------------------------------------------------------------------

describe("generatePhone", () => {
  it.each([
    ["MX", "+52", 10],
    ["ES", "+34", 9],
  ] as const)("makes full-length %s phones that match the contract, differ and never end in zeros", (country, prefix, length) => {
    const phones = Array.from({ length: 200 }, () => generatePhone(country));
    for (const phone of phones) {
      expect(phone).toMatch(/^\+(34|52)[0-9]{8,11}$/);
      expect(phone.startsWith(prefix)).toBe(true);
      expect(phone.length).toBe(prefix.length + length);
      expect(phone.slice(prefix.length + length - 8)).not.toMatch(/^0+$/);
      expect(phone).not.toMatch(/^\+5255000000[0-9]{2}$/);
      expect(isValidPhone(phone, country)).toBe(true);
      expect(countryOfPhone(phone)).toBe(country);
    }
    if (country === "ES") for (const phone of phones) expect(["6", "7"]).toContain(phone.charAt(3));
    expect(new Set(phones).size).toBeGreaterThanOrEqual(190);
  });

  it("never starts the 8 digit tail with 0, even when the random source returns 0 or just under 1", () => {
    for (const random of [() => 0, () => 0.999999]) {
      for (const country of ["MX", "ES"] as const) {
        const phone = generatePhone(country, random);
        expect(isValidPhone(phone, country)).toBe(true);
        expect(phone.slice(-8)).not.toMatch(/^0/);
      }
    }
  });
});

describe("isValidPhone", () => {
  it("accepts the contract's pattern and checks the country's prefix when one is given", () => {
    expect(isValidPhone("+525512345678")).toBe(true);
    expect(isValidPhone("+34612345678")).toBe(true);
    expect(isValidPhone("+525512345678", "ES")).toBe(false);
    expect(isValidPhone("+34612345678", "MX")).toBe(false);
  });

  it.each(["", "525512345678", "+5255", "+52551234567890123", "+1 555 123 4567", " +525512345678", "+52551234567a"])("rejects %j", (phone) => {
    expect(isValidPhone(phone)).toBe(false);
  });
});

describe("matchByPhone", () => {
  const withPhone = (phone: string, over: Parameters<typeof summary>[0] = {}) => {
    const base = summary(over);
    return { ...base, candidate: { ...base.candidate, phone } };
  };

  it("matches the whole phone, never a prefix of it or a number it is a prefix of", () => {
    const short = withPhone("+52551234567", { id: "app_short" });
    const long = withPhone("+525512345678", { id: "app_long" });
    expect(matchByPhone([short, long], "+525512345678")?.id).toBe("app_long");
    expect(matchByPhone([short, long], "+52551234567")?.id).toBe("app_short");
    expect(matchByPhone([short], "+525512345678")).toBeNull();
    expect(matchByPhone([long], "+52551234567")).toBeNull();
    expect(matchByPhone([withPhone(" +525512345678")], "+525512345678")).toBeNull();
  });

  it("takes the open one, then the newest, among exact matches", () => {
    const closed = withPhone("+525512345678", { id: "app_closed", status: "closed", created_at: "2026-10-09T12:00:00Z" });
    const open = withPhone("+525512345678", { id: "app_open", status: "open", created_at: "2026-10-09T10:00:00Z" });
    const newerOpen = withPhone("+525512345678", { id: "app_newer", status: "open", created_at: "2026-10-09T11:00:00Z" });
    expect(matchByPhone([closed, open], "+525512345678")?.id).toBe("app_open");
    expect(matchByPhone([open, newerOpen, closed], "+525512345678")?.id).toBe("app_newer");
    expect(matchByPhone([], "+525512345678")).toBeNull();
  });
});

describe("tickState", () => {
  const sent = msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" });
  const agent = msg({ id: 2, direction: "outbound", sender: "agent", text: "Hola" });

  it("is one tick when stored, two while the agent's run is still going, two accented once an agent or team message follows", () => {
    expect(tickState(sent, [sent], true)).toBe("sent");
    expect(tickState(sent, [sent], null)).toBe("sent");
    expect(tickState(sent, [sent], false)).toBe("delivered");
    expect(tickState(sent, [sent, agent], true)).toBe("read");
    expect(tickState(sent, [sent, agent], false)).toBe("read");
    const team = msg({ id: 3, direction: "outbound", sender: "human", text: "Hola, soy del equipo" });
    expect(tickState(sent, [sent, team], true)).toBe("read");
  });

  it("is not moved by a system notice or by an earlier message", () => {
    const notice = msg({ id: 3, direction: "outbound", sender: "system", text: "Un miembro del equipo se unió" });
    expect(tickState(sent, [sent, notice], true)).toBe("sent");
    const later = msg({ id: 5, direction: "inbound", sender: "candidate", text: "Otra" });
    expect(tickState(later, [sent, agent, later], true)).toBe("sent");
  });
});

describe("bubbleTime and initials", () => {
  it("shows HH:mm in the conversation country's time zone", () => {
    expect(bubbleTime("2026-10-09T10:00:10Z", "MX")).toBe("04:00");
    expect(bubbleTime("2026-10-09T10:00:10Z", "ES")).toBe("12:00");
    expect(bubbleTime("2026-10-09T23:30:00Z", "ES")).toBe("01:30");
    expect(bubbleTime("not a date", "MX")).toBe("");
  });

  it("formats a date and time in the country's zone, for the record and the clock", () => {
    expect(formatZoned("2026-10-09T10:04:00Z", "MX")).toBe("09 Oct, 04:04");
    expect(formatZoned("2026-10-09T10:04:00Z", "ES")).toBe("09 Oct, 12:04");
    expect(formatZoned("not a date", "MX")).toBe("");
  });

  it("drops the date when it is the clock's day in that zone, and keeps it for any other day", () => {
    const today = new Date("2026-10-09T16:00:00Z");
    expect(formatZoned("2026-10-09T10:04:00Z", "MX", today)).toBe("04:04");
    expect(formatZoned("2026-10-09T10:04:00Z", "ES", today)).toBe("12:04");
    // 02:00 Z on the 10th is still the 9th in Mexico City, but the 10th in Madrid.
    expect(formatZoned("2026-10-10T02:00:00Z", "MX", today)).toBe("20:00");
    expect(formatZoned("2026-10-10T02:00:00Z", "ES", today)).toBe("10 Oct, 04:00");
    expect(formatZoned("2026-10-08T10:04:00Z", "MX", today)).toBe("08 Oct, 04:04");
    expect(formatZoned("2026-10-09T10:04:00Z", "MX", null)).toBe("09 Oct, 04:04");
    expect(formatZoned("2026-10-09T10:04:00Z", "MX", undefined)).toBe("09 Oct, 04:04");
  });

  it("takes up to two initials", () => {
    expect(initials("Sazón Empleo")).toBe("SE");
    expect(initials("Sazón")).toBe("S");
    expect(initials("  ana  maría lópez ")).toBe("AM");
    expect(initials("")).toBe("");
  });
});

// ---------------------------------------------------------------------------------------------
// Phone pane (SP4, SP5, SP6, SP7)
// ---------------------------------------------------------------------------------------------

function fakeConversation(over: Partial<Conversation> = {}): Conversation {
  return {
    conversationId: CONV,
    messages: [],
    typing: false,
    runFinished: true,
    error: null,
    personaName: "Sazón Empleo",
    language: "es-MX",
    busy: false,
    answerableId: null,
    sent: 0,
    start: vi.fn(async () => true),
    resume: vi.fn(),
    send: vi.fn(async () => true),
    tap: vi.fn(async () => true),
    reset: vi.fn(),
    ...over,
  };
}

const MX_PHONE = "+525512345678";
const ES_PHONE = "+34612345678";

function pane(conversation: Conversation, extra: Partial<Parameters<typeof PhonePane>[0]> = {}) {
  const onCountryChange = vi.fn();
  const view = render(<PhonePane conversation={conversation} phone={MX_PHONE} onCountryChange={onCountryChange} {...extra} />);
  return { ...view, onCountryChange };
}

const bubbles = () => Array.from(document.querySelectorAll<HTMLElement>("[data-side]"));

describe("phone pane: frame and bubbles", () => {
  it("is a region named Phone with a log named Messages whose language follows the conversation", () => {
    pane(fakeConversation({ language: "en" }));
    const region = screen.getByRole("region", { name: "Phone" });
    const log = within(region).getByRole("log", { name: "Messages" });
    expect(log).toHaveAttribute("aria-live", "polite");
    expect(log).toHaveAttribute("lang", "en");
  });

  it("lets the log take keyboard focus, so a scrolled chat can be read without a pointer", () => {
    pane(fakeConversation());
    expect(screen.getByRole("log", { name: "Messages" })).toHaveAttribute("tabindex", "0");
  });

  it("falls back to the phone's country for the log's language", () => {
    pane(fakeConversation({ language: null }), { phone: ES_PHONE });
    expect(screen.getByRole("log", { name: "Messages" })).toHaveAttribute("lang", "es-ES");
  });

  it("orders messages by id and shows each once, from an out-of-order page", () => {
    const a = msg({ id: 3, direction: "outbound", sender: "agent", text: "tercero" });
    const b = msg({ id: 1, direction: "inbound", sender: "candidate", text: "primero" });
    const c = msg({ id: 2, direction: "outbound", sender: "agent", text: "segundo" });
    pane(fakeConversation({ messages: [a, b, c, b] }));
    expect(bubbles().map((el) => el.textContent)).toEqual([
      expect.stringContaining("primero"),
      expect.stringContaining("segundo"),
      expect.stringContaining("tercero"),
    ]);
  });

  it("puts the Candidate on the right, the agent and the team on the left, and a system notice in the middle", () => {
    pane(
      fakeConversation({
        messages: [
          msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" }),
          msg({ id: 2, direction: "outbound", sender: "agent", sender_label: "Sazón Empleo", text: "Buenas" }),
          msg({ id: 3, direction: "outbound", sender: "system", text: "Un miembro del equipo se unió" }),
          msg({ id: 4, direction: "outbound", sender: "human", sender_label: "Equipo de Grupo Sazón", text: "Soy del equipo" }),
        ],
      }),
    );
    expect(bubbles().map((el) => el.getAttribute("data-side"))).toEqual(["right", "left", "center", "left"]);
    const team = bubbles()[3];
    expect(within(team).getByText("Equipo de Grupo Sazón")).toBeInTheDocument();
  });

  it("shows initials, the persona's name and online in the header, and a neutral name when the channel sent none", () => {
    const view = pane(fakeConversation());
    const header = screen.getByTestId("phone-header");
    expect(within(header).getByText("SE")).toBeInTheDocument();
    expect(within(header).getByText("Sazón Empleo")).toBeInTheDocument();
    expect(within(header).getByText("online")).toBeInTheDocument();
    view.unmount();
    pane(fakeConversation({ personaName: null }));
    expect(within(screen.getByTestId("phone-header")).getByText("Sazón Empleo")).toBeInTheDocument();
  });

  it("keeps one persona name for the whole Take: the start card, the chat and the next start card agree", () => {
    const header = () => within(screen.getByTestId("phone-header"));
    const view = pane(fakeConversation({ conversationId: null, personaName: null }));
    const before = header().getByText(/Sazón Empleo/);
    expect(before).toBeInTheDocument();
    const started = (over: Partial<Conversation>) => (
      <PhonePane conversation={fakeConversation(over)} phone={MX_PHONE} onCountryChange={() => undefined} />
    );
    // The channel names the agent when the conversation starts: the same name, no switch.
    view.rerender(started({ personaName: "Sazón Empleo" }));
    expect(header().getByText("Sazón Empleo")).toBeInTheDocument();
    // A channel that names the agent otherwise is followed, and then kept into the next start card.
    view.rerender(started({ personaName: "Clara de Sazón" }));
    expect(header().getByText("Clara de Sazón")).toBeInTheDocument();
    view.rerender(started({ conversationId: null, personaName: null }));
    expect(header().getByText("Clara de Sazón")).toBeInTheDocument();
    expect(header().queryByText("Sazón Empleo")).not.toBeInTheDocument();
  });

  it("shows typing… in the header and three dots in the log while the agent's turn is not over", () => {
    const view = pane(fakeConversation({ typing: true, runFinished: false }));
    expect(within(screen.getByTestId("phone-header")).getByText("typing…")).toBeInTheDocument();
    expect(within(screen.getByRole("log", { name: "Messages" })).getByTestId("phone-typing").children).toHaveLength(3);
    view.rerender(<PhonePane conversation={fakeConversation({ typing: false })} phone={MX_PHONE} onCountryChange={() => undefined} />);
    expect(screen.queryByText("typing…")).not.toBeInTheDocument();
    expect(screen.queryByTestId("phone-typing")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("phone-header")).getByText("online")).toBeInTheDocument();
  });

  it("shows each bubble's time in the conversation country's zone", () => {
    const messages = [msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola", created_at: "2026-10-09T10:00:10Z" })];
    const view = pane(fakeConversation({ messages }));
    expect(within(bubbles()[0]).getByText("04:00")).toBeInTheDocument();
    view.unmount();
    pane(fakeConversation({ messages }), { phone: ES_PHONE });
    expect(within(bubbles()[0]).getByText("12:00")).toBeInTheDocument();
  });

  it("names the tick Sent, Delivered or Read from the thread", () => {
    const hello1 = msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" });
    const view = pane(fakeConversation({ messages: [hello1], runFinished: true }));
    expect(screen.getByRole("img", { name: "Sent" })).toBeInTheDocument();
    view.rerender(<PhonePane conversation={fakeConversation({ messages: [hello1], runFinished: false })} phone={MX_PHONE} onCountryChange={() => undefined} />);
    expect(screen.getByRole("img", { name: "Delivered" })).toBeInTheDocument();
    const reply = msg({ id: 2, direction: "outbound", sender: "agent", text: "Buenas" });
    view.rerender(<PhonePane conversation={fakeConversation({ messages: [hello1, reply] })} phone={MX_PHONE} onCountryChange={() => undefined} />);
    expect(screen.getByRole("img", { name: "Read" })).toBeInTheDocument();
    // Only the Candidate's own bubbles carry a tick.
    expect(screen.getAllByRole("img", { name: /^(Sent|Delivered|Read)$/ })).toHaveLength(1);
  });

  it("shows the empty-chat line once a conversation exists and nothing has been said", () => {
    pane(fakeConversation({ messages: [] }));
    expect(within(screen.getByRole("log", { name: "Messages" })).getByText("Say hello to start. The Candidate writes first.")).toBeInTheDocument();
  });

  it("renders no name, logo or image of the chat app it imitates", () => {
    pane(fakeConversation({ messages: [msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" })] }));
    expect(document.body.innerHTML).not.toMatch(/whatsapp/i);
    expect(document.querySelectorAll("img")).toHaveLength(0);
  });
});

describe("phone pane: interactive messages", () => {
  const olderButtons = msg({ id: 10, direction: "outbound", sender: "agent", text: "¿Cómo te llamas?", interactive: { style: "buttons", options: [{ id: "skip", label: "Omitir" }] } });

  it("enables only the answerable message's reply buttons and disables every earlier one", () => {
    pane(fakeConversation({ messages: [olderButtons, question], answerableId: 2 }));
    expect(screen.getByRole("button", { name: "Omitir" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sí" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "No" })).toBeEnabled();
  });

  it("disables the latest options once the Candidate wrote again (answerableId is null)", () => {
    pane(fakeConversation({ messages: [question, msg({ id: 3, direction: "inbound", sender: "candidate", text: "Sí" })], answerableId: null }));
    expect(screen.getByRole("button", { name: "Sí" })).toBeDisabled();
  });

  it("taps an option through the conversation with that message and the option's id", () => {
    const conversation = fakeConversation({ messages: [question], answerableId: 2 });
    pane(conversation);
    fireEvent.click(screen.getByRole("button", { name: "Sí" }));
    expect(conversation.tap).toHaveBeenCalledWith(question, "yes");
  });

  it("sends {in_reply_to, option_id} and counts the tap, with the real hook", async () => {
    const mock = mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 2, agent_typing: false, run_finished: true, messages: [question] }),
      "POST /api/chat/conversations/:id/replies": [202, { message_id: "r", id: 3, duplicate: false }],
    });
    renderHost();
    act(() => sessionRef.current!.conversation.resume(CONV));
    fireEvent.click(await screen.findByRole("button", { name: "Sí" }));
    await waitFor(() => expect(mock.find("POST", `/api/chat/conversations/${CONV}/replies`)).toHaveLength(1));
    expect(mock.find("POST", `/api/chat/conversations/${CONV}/replies`)[0].body).toMatchObject({ in_reply_to: 2, option_id: "yes" });
    await waitFor(() => expect(sessionRef.current!.conversation.sent).toBe(1));
  });

  it("shows the server's message in an alert when the options expired (409 option_expired)", async () => {
    mockApi({
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 2, agent_typing: false, run_finished: true, messages: [question] }),
      "POST /api/chat/conversations/:id/replies": error(409, "option_expired", "Esas opciones ya no están disponibles. Escribe tu respuesta, por favor."),
    });
    renderHost();
    act(() => sessionRef.current!.conversation.resume(CONV));
    fireEvent.click(await screen.findByRole("button", { name: "Sí" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Esas opciones ya no están disponibles.");
  });

  describe("list", () => {
    const list = msg({
      id: 20,
      direction: "outbound",
      sender: "agent",
      text: "¿En qué zona vives?",
      interactive: { style: "list", options: [{ id: "z1", label: "Centro" }, { id: "z2", label: "Angelópolis" }, { id: "z3", label: "La Paz" }] },
    });

    it("shows one Options button on the bubble and the options only in the sheet", () => {
      pane(fakeConversation({ messages: [list], answerableId: 20 }));
      expect(screen.getByRole("button", { name: "Options" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Centro" })).not.toBeInTheDocument();
    });

    it("opens the sheet inside the phone, moves focus into it, closes on Escape and returns focus to Options", () => {
      pane(fakeConversation({ messages: [list], answerableId: 20 }));
      const trigger = screen.getByRole("button", { name: "Options" });
      trigger.focus();
      fireEvent.click(trigger);
      const sheet = screen.getByRole("dialog", { name: "Options" });
      expect(screen.getByRole("region", { name: "Phone" })).toContainElement(sheet);
      expect(sheet).toHaveAttribute("aria-modal", "false");
      expect(within(sheet).getAllByRole("button")).toHaveLength(3);
      expect(within(sheet).getByRole("button", { name: "Centro" })).toHaveFocus();
      expect(trigger).toHaveAttribute("aria-expanded", "true");

      fireEvent.keyDown(within(sheet).getByRole("button", { name: "Centro" }), { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "Options" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
      expect(trigger).toHaveAttribute("aria-expanded", "false");
    });

    it("taps the chosen option, closes the sheet and returns focus", () => {
      const conversation = fakeConversation({ messages: [list], answerableId: 20 });
      pane(conversation);
      const trigger = screen.getByRole("button", { name: "Options" });
      fireEvent.click(trigger);
      fireEvent.click(within(screen.getByRole("dialog", { name: "Options" })).getByRole("button", { name: "Angelópolis" }));
      expect(conversation.tap).toHaveBeenCalledWith(list, "z2");
      expect(screen.queryByRole("dialog", { name: "Options" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("puts the Options button in the bubble as its footer, not beside it", () => {
      pane(fakeConversation({ messages: [list], answerableId: 20 }));
      const trigger = screen.getByRole("button", { name: "Options" });
      expect(trigger.closest(".phone-bubble")).not.toBeNull();
      expect(trigger).toHaveClass("phone-list");
    });

    it("shows the option the Candidate chose in the spent footer, and only Options when it was typed", () => {
      const chosen = msg({ id: 21, direction: "inbound", sender: "candidate", text: "Angelópolis", reply_to: { message_id: 20, option_id: "z2" } });
      const view = pane(fakeConversation({ messages: [list, chosen], answerableId: null }));
      const footer = screen.getByRole("button", { name: /Options/ });
      expect(footer).toBeDisabled();
      expect(footer).toHaveTextContent("Angelópolis");
      view.unmount();
      const typed = msg({ id: 21, direction: "inbound", sender: "candidate", text: "La Paz" });
      pane(fakeConversation({ messages: [list, typed], answerableId: null }));
      expect(screen.getByRole("button", { name: "Options" })).toBeDisabled();
      expect(screen.queryByText("La Paz", { selector: ".phone-list *" })).not.toBeInTheDocument();
    });

    it("disables the Options button of a list that is no longer answerable, and does not trap focus", () => {
      pane(fakeConversation({ messages: [list], answerableId: null }));
      expect(screen.getByRole("button", { name: "Options" })).toBeDisabled();
    });

    it("closes the sheet when the list stops being answerable", () => {
      const view = pane(fakeConversation({ messages: [list], answerableId: 20 }));
      fireEvent.click(screen.getByRole("button", { name: "Options" }));
      expect(screen.getByRole("dialog", { name: "Options" })).toBeInTheDocument();
      view.rerender(<PhonePane conversation={fakeConversation({ messages: [list], answerableId: null })} phone={MX_PHONE} onCountryChange={() => undefined} />);
      expect(screen.queryByRole("dialog", { name: "Options" })).not.toBeInTheDocument();
    });
  });
});

describe("phone pane: .ics card and links", () => {
  it("renders an ics attachment as a card whose link is named by the file and downloads it", () => {
    pane(
      fakeConversation({
        messages: [
          msg({
            id: 21,
            direction: "outbound",
            sender: "agent",
            text: "Tu entrevista quedó.",
            attachments: [{ type: "ics", url: "/api/chat/x/invite.ics", filename: "entrevista.ics" }],
          }),
        ],
      }),
    );
    const link = screen.getByRole("link", { name: "entrevista.ics" });
    expect(link).toHaveAttribute("href", "/api/chat/x/invite.ics");
    expect(link).toHaveAttribute("download", "entrevista.ics");
    expect(link).toHaveTextContent("Interview invite");
  });

  it("renders a link entry as an anchor with its label", () => {
    pane(
      fakeConversation({
        messages: [msg({ id: 22, direction: "outbound", sender: "agent", text: "Reserva aquí", links: [{ url: "https://sazon.example/book/abc", label: "Reservar entrevista" }] })],
      }),
    );
    expect(screen.getByRole("link", { name: "Reservar entrevista" })).toHaveAttribute("href", "https://sazon.example/book/abc");
  });

  it("never fetches the file itself", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    pane(
      fakeConversation({
        messages: [msg({ id: 21, direction: "outbound", sender: "agent", text: "x", attachments: [{ type: "ics", url: "/api/chat/x/invite.ics", filename: "entrevista.ics" }] })],
      }),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("phone pane: composer", () => {
  it("has the label Message and the button Send, and sends the text through the conversation", async () => {
    const conversation = fakeConversation();
    pane(conversation);
    const input = screen.getByLabelText("Message");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "Hola, soy Ana" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(conversation.send).toHaveBeenCalledWith("Hola, soy Ana"));
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("styles Send as the quiet button until there is something to send", () => {
    pane(fakeConversation());
    expect(screen.getByRole("button", { name: "Send" })).toHaveClass("ui-btn--secondary");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hola" } });
    expect(screen.getByRole("button", { name: "Send" })).toHaveClass("ui-btn--primary");
  });

  it("puts the text back when the send failed", async () => {
    const conversation = fakeConversation({ send: vi.fn(async () => false) });
    pane(conversation);
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "Hola" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(conversation.send).toHaveBeenCalled());
    await waitFor(() => expect(input).toHaveValue("Hola"));
  });

  it("shows a chat error in an alert", () => {
    pane(fakeConversation({ error: "Lost the connection. Retrying." }));
    expect(screen.getByRole("alert")).toHaveTextContent("Lost the connection. Retrying.");
  });

  it("is uncontrolled without a draft, and controlled with draft and onDraftChange", () => {
    const view = pane(fakeConversation());
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "libre" } });
    expect(screen.getByLabelText("Message")).toHaveValue("libre");
    view.unmount();

    const onDraftChange = vi.fn();
    const controlled = pane(fakeConversation(), { draft: "escrito por el jugador", onDraftChange });
    expect(screen.getByLabelText("Message")).toHaveValue("escrito por el jugador");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "otro" } });
    expect(onDraftChange).toHaveBeenCalledWith("otro");
    // Controlled: the value is whatever the parent passes.
    expect(screen.getByLabelText("Message")).toHaveValue("escrito por el jugador");
    controlled.rerender(<PhonePane conversation={fakeConversation()} phone={MX_PHONE} onCountryChange={() => undefined} draft="otro" onDraftChange={onDraftChange} />);
    expect(screen.getByLabelText("Message")).toHaveValue("otro");
  });

  it("clears a controlled draft through onDraftChange after a send", async () => {
    const onDraftChange = vi.fn();
    const conversation = fakeConversation();
    pane(conversation, { draft: "Hola", onDraftChange });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(conversation.send).toHaveBeenCalledWith("Hola"));
    expect(onDraftChange).toHaveBeenCalledWith("");
  });
});

describe("phone pane: start card (SP7)", () => {
  it("shows the start card, not the chat, before a conversation: Country (Mexico by default), the phone read-only, Start", () => {
    pane(fakeConversation({ conversationId: null, personaName: null }));
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Country")).toHaveValue("MX");
    // The phone is a plain value (a label and a figure), not a field that looks editable.
    expect(screen.getByText("Candidate's phone number")).toBeInTheDocument();
    expect(screen.getByTestId("start-phone")).toHaveTextContent(MX_PHONE);
    expect(screen.queryByLabelText("Candidate's phone number")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start conversation" })).toBeEnabled();
  });

  it("starts with exactly {country, locale, phone, client: 'demo'}", async () => {
    const conversation = fakeConversation({ conversationId: null });
    pane(conversation);
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    await waitFor(() => expect(conversation.start).toHaveBeenCalledTimes(1));
    expect(conversation.start).toHaveBeenCalledWith({ country: "MX", locale: "es-MX", phone: MX_PHONE, client: "demo" });
  });

  it("starts a Spanish phone as Spain", async () => {
    const conversation = fakeConversation({ conversationId: null });
    pane(conversation, { phone: ES_PHONE });
    expect(screen.getByLabelText("Country")).toHaveValue("ES");
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    await waitFor(() => expect(conversation.start).toHaveBeenCalledWith({ country: "ES", locale: "es-ES", phone: ES_PHONE, client: "demo" }));
  });

  it("sends no request without a valid phone", () => {
    for (const phone of ["", "+5255", "5512345678"]) {
      const conversation = fakeConversation({ conversationId: null });
      const view = pane(conversation, { phone });
      expect(screen.getByRole("button", { name: "Start conversation" })).toBeDisabled();
      fireEvent.submit(screen.getByRole("form", { name: "New Take" }));
      expect(conversation.start).not.toHaveBeenCalled();
      view.unmount();
    }
  });

  it("asks for a fresh phone of the other country when Country changes", () => {
    const { onCountryChange } = pane(fakeConversation({ conversationId: null }));
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "ES" } });
    expect(onCountryChange).toHaveBeenCalledWith("ES");
  });

  it("disables Start and shows it busy while the conversation is starting", () => {
    pane(fakeConversation({ conversationId: null, busy: true }));
    expect(screen.getByRole("button", { name: "Start conversation" })).toBeDisabled();
  });

  it("shows the start error in an alert", () => {
    pane(fakeConversation({ conversationId: null, error: "That conversation no longer exists. Start a new one." }));
    expect(screen.getByRole("alert")).toHaveTextContent("That conversation no longer exists.");
  });
});

// ---------------------------------------------------------------------------------------------
// Stage session (SP7) and a real-hook host
// ---------------------------------------------------------------------------------------------

const sessionRef: { current: ReturnType<typeof useStageSession> | null } = { current: null };

function renderHost() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
      <SessionHost />
    </QueryClientProvider>,
  );
}

function SessionHost() {
  const session = useStageSession();
  useEffect(() => {
    sessionRef.current = session;
  });
  return <PhonePane conversation={session.conversation} phone={session.phone} onCountryChange={(country) => session.newTake(country)} />;
}

describe("useStageSession", () => {
  it("starts on a Mexican phone and a Mexican locale, and never reads sessionStorage", () => {
    const get = vi.spyOn(Storage.prototype, "getItem");
    const set = vi.spyOn(Storage.prototype, "setItem");
    renderHost();
    const session = sessionRef.current!;
    expect(session.country).toBe("MX");
    expect(session.locale).toBe("es-MX");
    expect(isValidPhone(session.phone, "MX")).toBe(true);
    expect(session.conversation.conversationId).toBeNull();
    expect(get).not.toHaveBeenCalled();
    expect(set).not.toHaveBeenCalled();
    get.mockRestore();
    set.mockRestore();
  });

  it("newTake makes a fresh phone and forgets the conversation; with a country it switches country and locale", async () => {
    mockApi({
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
    });
    renderHost();
    const first = sessionRef.current!.phone;
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    await waitFor(() => expect(sessionRef.current!.conversation.conversationId).toBe(CONV));

    act(() => sessionRef.current!.newTake());
    expect(sessionRef.current!.phone).not.toBe(first);
    expect(sessionRef.current!.country).toBe("MX");
    expect(sessionRef.current!.conversation.conversationId).toBeNull();

    act(() => sessionRef.current!.newTake("ES"));
    expect(sessionRef.current!.country).toBe("ES");
    expect(sessionRef.current!.locale).toBe("es-ES");
    expect(isValidPhone(sessionRef.current!.phone, "ES")).toBe(true);
    expect(screen.getByLabelText("Country")).toHaveValue("ES");
  });

  it("starts the conversation with the session's phone and the demo client", async () => {
    const mock = mockApi({
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo", language: "es-MX" }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
    });
    renderHost();
    const phone = sessionRef.current!.phone;
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    expect(await screen.findByRole("log", { name: "Messages" })).toBeInTheDocument();
    expect(mock.find("POST", "/api/chat/conversations")[0].body).toEqual({ country: "MX", locale: "es-MX", phone, client: "demo" });
    expect(within(screen.getByTestId("phone-header")).getByText("Sazón Empleo")).toBeInTheDocument();
  });

  it("asks for the Application only after the Candidate has written, by the session's whole phone", async () => {
    const mock = mockApi({
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
      "POST /api/chat/conversations/:id/messages": [202, { message_id: "m", id: 1, duplicate: false }],
      "GET /api/applications": { items: [], next_cursor: null, total: 0 },
    });
    renderHost();
    const phone = sessionRef.current!.phone;
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    await screen.findByRole("log", { name: "Messages" });
    // A conversation exists, but nobody has written yet: no lookup.
    await act(async () => new Promise((resolve) => setTimeout(resolve, 60)));
    expect(mock.find("GET", "/api/applications")).toHaveLength(0);
    expect(sessionRef.current!.application.started).toBe(false);

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Hola" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(mock.find("GET", "/api/applications").length).toBeGreaterThan(0));
    expect(mock.find("GET", "/api/applications")[0].query.get("q")).toBe(phone);
    expect(sessionRef.current!.application.started).toBe(true);
  });

  it("starts a new Take with the lookup idle again", async () => {
    const mock = mockApi({
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 0, agent_typing: false, run_finished: true, messages: [] }),
      "POST /api/chat/conversations/:id/messages": [202, { message_id: "m", id: 1, duplicate: false }],
      "GET /api/applications": { items: [], next_cursor: null, total: 0 },
    });
    renderHost();
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "Hola" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sessionRef.current!.application.started).toBe(true));
    act(() => sessionRef.current!.newTake());
    expect(sessionRef.current!.application.started).toBe(false);
    expect(sessionRef.current!.application.application).toBeNull();
    const before = mock.find("GET", "/api/applications").length;
    await act(async () => new Promise((resolve) => setTimeout(resolve, 60)));
    expect(mock.find("GET", "/api/applications")).toHaveLength(before);
  });
});

// ---------------------------------------------------------------------------------------------
// Record lookup (SP8)
// ---------------------------------------------------------------------------------------------

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function LookupHost(props: { phone: string; enabled: boolean; onValue: (lookup: ApplicationLookup) => void }) {
  const lookup = useApplicationLookup({ phone: props.phone, enabled: props.enabled });
  const { onValue } = props;
  useEffect(() => {
    onValue(lookup);
  });
  return <p data-testid="matched">{lookup.application?.id ?? "none"}</p>;
}

const withPhone = (phone: string, over: Parameters<typeof summary>[0] = {}): ApplicationSummary => {
  const base = summary(over);
  return { ...base, candidate: { ...base.candidate, phone } };
};

describe("useApplicationLookup", () => {
  const PHONE = "+525512345678";
  let latest: ApplicationLookup | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    latest = null;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const lookup = () => latest as ApplicationLookup;
  const flush = (ms = 0) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
  const mount = (enabled = true, phone = PHONE) => {
    const view = render(
      <QueryClientProvider client={newClient()}>
        <LookupHost
          phone={phone}
          enabled={enabled}
          onValue={(v) => {
            latest = v;
          }}
        />
      </QueryClientProvider>,
    );
    const again = (next: { enabled?: boolean; phone?: string }) =>
      view.rerender(
        <QueryClientProvider client={newClient()}>
          <LookupHost
            phone={next.phone ?? phone}
            enabled={next.enabled ?? enabled}
            onValue={(v) => {
              latest = v;
            }}
          />
        </QueryClientProvider>,
      );
    return { ...view, again };
  };
  const list = (items: ApplicationSummary[]) => ({ items, next_cursor: null, total: items.length });

  it("matches the whole phone and never a prefix: of two phones that share a prefix only the exact one shows", async () => {
    const short = withPhone("+52551234567", { id: "app_short" });
    const exact = withPhone("+525512345678", { id: "app_exact" });
    mockApi({ "GET /api/applications": list([short, exact]) });
    mount();
    await flush();
    expect(screen.getByTestId("matched")).toHaveTextContent("app_exact");
  });

  it("shows the waiting state and no record when no phone matches exactly", async () => {
    mockApi({ "GET /api/applications": list([withPhone("+52551234567", { id: "app_short" }), withPhone("+5255123456789", { id: "app_long" })]) });
    mount();
    await flush();
    expect(screen.getByTestId("matched")).toHaveTextContent("none");
    expect(lookup().started).toBe(true);
    expect(lookup().isError).toBe(false);
  });

  it("takes the open Application, not the closed one, when the phone matches two", async () => {
    mockApi({
      "GET /api/applications": list([
        withPhone(PHONE, { id: "app_closed", status: "closed", created_at: "2026-10-09T12:00:00Z" }),
        withPhone(PHONE, { id: "app_open", status: "open", created_at: "2026-10-09T10:00:00Z" }),
      ]),
    });
    mount();
    await flush();
    expect(screen.getByTestId("matched")).toHaveTextContent("app_open");
  });

  it("asks with the whole phone as the one q, a limit of 50, and nothing else", async () => {
    const mock = mockApi({ "GET /api/applications": list([]) });
    mount();
    await flush();
    const call = mock.find("GET", "/api/applications")[0];
    // A plus that reached the server as %2B decodes to a plus; a bare plus would decode to a space.
    expect(call.query.get("q")).toBe(PHONE);
    expect(call.query.get("limit")).toBe("50");
    expect([...call.query.keys()].sort()).toEqual(["limit", "q"]);
  });

  it("sends no request until the Candidate has written", async () => {
    const mock = mockApi({ "GET /api/applications": list([]) });
    const view = mount(false);
    await flush(5000);
    expect(mock.find("GET", "/api/applications")).toHaveLength(0);
    expect(lookup().started).toBe(false);
    view.unmount();
  });

  it("asks again every second until a match appears, then stops", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/applications": () => {
        calls += 1;
        return list(calls < 3 ? [] : [withPhone(PHONE, { id: "app_exact" })]);
      },
    });
    mount();
    await flush();
    expect(mock.find("GET", "/api/applications")).toHaveLength(1);
    await flush(1000);
    expect(mock.find("GET", "/api/applications")).toHaveLength(2);
    await flush(1000);
    expect(mock.find("GET", "/api/applications")).toHaveLength(3);
    await flush(10);
    expect(screen.getByTestId("matched")).toHaveTextContent("app_exact");
    await flush(3000);
    expect(mock.find("GET", "/api/applications")).toHaveLength(3);
  });

  it("shows an error, does not poll a failing list, and asks once more on retry", async () => {
    let failing = true;
    const mock = mockApi({
      "GET /api/applications": () => (failing ? error(500, "internal", "boom") : list([withPhone(PHONE, { id: "app_exact" })])),
    });
    mount();
    await flush();
    expect(lookup().isError).toBe(true);
    expect(lookup().error?.message).toBe("boom");
    await flush(3000);
    expect(mock.find("GET", "/api/applications")).toHaveLength(1);
    failing = false;
    act(() => lookup().retry());
    await flush();
    expect(mock.find("GET", "/api/applications")).toHaveLength(2);
    expect(screen.getByTestId("matched")).toHaveTextContent("app_exact");
  });

  it("starts over for a new phone", async () => {
    const mock = mockApi({
      "GET /api/applications": (call) => list(call.query.get("q") === PHONE ? [withPhone(PHONE, { id: "app_first" })] : []),
    });
    const view = mount();
    await flush();
    expect(screen.getByTestId("matched")).toHaveTextContent("app_first");
    view.again({ phone: "+525599988877" });
    await flush();
    expect(screen.getByTestId("matched")).toHaveTextContent("none");
    expect(mock.find("GET", "/api/applications").some((c) => c.query.get("q") === "+525599988877")).toBe(true);
  });
});

// ---------------------------------------------------------------------------------------------
// Record pane (SP9, SP10, SP11, SP12)
// ---------------------------------------------------------------------------------------------

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  private readonly listeners = new Map<string, Set<(event: Event) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: Event) => void) {
    const set = this.listeners.get(name) ?? new Set();
    set.add(listener);
    this.listeners.set(name, set);
  }

  removeEventListener(name: string, listener: (event: Event) => void) {
    this.listeners.get(name)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  emit(name: string, data?: unknown) {
    const event = data === undefined ? new Event(name) : new MessageEvent(name, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }
}

const APP = summary();
const DETAIL_PATH = `/api/applications/${APP.id}`;

function lookupOf(over: Partial<ApplicationLookup> = {}): ApplicationLookup {
  return { application: APP, started: true, isError: false, error: null, retry: vi.fn(), ...over };
}

function renderRecord(current: { value: ApplicationDetail | (() => unknown) }, over: Partial<ApplicationLookup> = {}) {
  const mock = mockApi({ [`GET ${DETAIL_PATH}`]: () => (typeof current.value === "function" ? current.value() : current.value) as object });
  const client = newClient();
  const wrap = (lookup: ApplicationLookup) => (
    <QueryClientProvider client={client}>
      <RecordPane lookup={lookup} />
    </QueryClientProvider>
  );
  const view = render(wrap(lookupOf(over)));
  return { ...view, mock, client, rerenderWith: (next: Partial<ApplicationLookup>) => view.rerender(wrap(lookupOf(next))) };
}

const row = (field: string) => document.querySelector<HTMLElement>(`[data-field="${field}"]`) as HTMLElement;
const detailCalls = (mock: ReturnType<typeof mockApi>) => mock.find("GET", DETAIL_PATH).length;
const settled = () => Array.from(document.querySelectorAll("[data-settle]"));
// jsdom has no AnimationEvent, so React listens for the prefixed name there; browsers fire the plain one.
const endAnimation = (el: Element) => {
  for (const name of ["animationend", "webkitAnimationEnd"]) fireEvent(el, new Event(name, { bubbles: true }));
};

async function agentView(current: { value: ApplicationDetail }, over: Partial<ApplicationLookup> = {}) {
  const view = renderRecord(current, over);
  await screen.findByRole("heading", { name: "Luis Hernández" });
  return view;
}

describe("record pane: states", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("is a region named Record that says what appears there before the first message, and asks for nothing", () => {
    const mock = mockApi({});
    render(
      <QueryClientProvider client={newClient()}>
        <RecordPane lookup={lookupOf({ application: null, started: false })} />
      </QueryClientProvider>,
    );
    const region = screen.getByRole("region", { name: "Record" });
    expect(region).toHaveTextContent("The Application appears here after the Candidate's first message");
    expect(mock.calls).toHaveLength(0);
    // Nothing is loading yet, so there is no skeleton: a still outline of the slots that will fill in.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    const ghost = region.querySelector(".record-ghost") as HTMLElement;
    expect(ghost).toHaveAttribute("aria-hidden", "true");
    for (const label of ["Claim", "Control", "Locations", "Zone", "Interview", "Full name", "Driver's license", "Start date"]) {
      expect(within(ghost).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(within(ghost).getAllByText("—")).toHaveLength(13);
  });

  it("shows a waiting state, not a record, while the lookup finds no match", () => {
    const mock = mockApi({});
    render(
      <QueryClientProvider client={newClient()}>
        <RecordPane lookup={lookupOf({ application: null, started: true })} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("status", { name: "Waiting for the Application" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(mock.calls).toHaveLength(0);
  });

  it("shows an error with Retry when the lookup failed, and Retry asks the lookup again", () => {
    const retry = vi.fn();
    mockApi({});
    render(
      <QueryClientProvider client={newClient()}>
        <RecordPane lookup={lookupOf({ application: null, isError: true, error: new Error("boom"), retry })} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not look up the Application");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("shows a skeleton while the first detail loads", async () => {
    renderRecord({ value: () => new Promise<object>(() => undefined) });
    expect(await screen.findByRole("status", { name: "Loading the Application" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("shows an error with Retry when the detail fails, and Retry asks once more", async () => {
    let failing = true;
    const { mock } = renderRecord({ value: () => (failing ? error(500, "internal", "boom") : detail()) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the Application");
    expect(detailCalls(mock)).toBe(1);
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Luis Hernández" });
    expect(detailCalls(mock)).toBe(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("record pane: data", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("shows the Candidate, the stage and the eight answers with their values and statuses", async () => {
    await agentView({ value: detail() });
    expect(screen.getByText("+525512345678")).toBeInTheDocument();
    expect(screen.getByTestId("record-stage")).toHaveTextContent("Qualified");
    const expected: [string, string, string][] = [
      ["full_name", "Luis Hernández", "Valid"],
      ["zone", "Centro, Puebla", "Valid"],
      ["driver_license", "Yes", "Valid"],
      ["availability", "Full time", "Valid"],
      ["preferred_schedule", "Morning, Afternoon", "Valid"],
      ["experience_years", "2", "Valid"],
      ["experience_platforms", "Rappi, Didi food", "Valid"],
      ["start_date", "12 Oct", "Flagged"],
    ];
    expect(document.querySelectorAll("[data-field]")).toHaveLength(8);
    expect(Array.from(document.querySelectorAll("[data-field]")).map((el) => el.getAttribute("data-field"))).toEqual(expected.map(([field]) => field));
    for (const [field, value, status] of expected) {
      expect(within(row(field)).getByText(value)).toBeInTheDocument();
      // Only an answer that is not valid carries a tag; a valid one carries a quiet check named Valid.
      if (status === "Valid") {
        expect(within(row(field)).getByRole("img", { name: "Valid" })).toBeInTheDocument();
        expect(within(row(field)).queryByText("Valid")).not.toBeInTheDocument();
      } else {
        expect(within(row(field)).getByText(status)).toBeInTheDocument();
        expect(within(row(field)).queryByRole("img", { name: "Valid" })).not.toBeInTheDocument();
      }
    }
  });

  it("formats a date answer like the clock and the events: day and month, never ISO", async () => {
    const base = detail();
    await agentView({ value: detail({ answers: { ...base.answers, start_date: "2026-01-05" } }) });
    expect(within(row("start_date")).getByText("05 Jan")).toBeInTheDocument();
    expect(row("start_date")).not.toHaveTextContent("2026-01-05");
  });

  it("puts the validation check right after its value, in the same cell, at a readable size", async () => {
    await agentView({ value: detail() });
    for (const field of ["full_name", "zone", "driver_license"]) {
      const main = row(field).querySelector(".record-row__main") as HTMLElement;
      expect(main).not.toBeNull();
      expect(main).toContainElement(within(row(field)).getByRole("img", { name: "Valid" }));
      expect(main).toContainElement(row(field).querySelector(".record-row__value") as HTMLElement);
    }
    // A non-valid answer keeps its Tag in the same place.
    expect(row("start_date").querySelector(".record-row__main")).toContainElement(within(row("start_date")).getByText("Flagged"));
  });

  it("shows the note of a flagged answer", async () => {
    await agentView({ value: detail() });
    expect(within(row("start_date")).getByText(/more than 60 days out/)).toBeInTheDocument();
  });

  it("shows Pending and a dash for a field with no entry and no value", async () => {
    const base = detail();
    await agentView({
      value: detail({
        answers: { ...base.answers, zone: null },
        answer_status: base.answer_status.filter((a) => a.field !== "zone"),
      }),
    });
    expect(within(row("zone")).getByText("-")).toBeInTheDocument();
    expect(within(row("zone")).getByText("Pending")).toBeInTheDocument();
  });

  it("shows the Claim, or Unclaimed", async () => {
    const view = await agentView({ value: detail({ claim: claimByAna }) });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Ana Torres");
    expect(screen.getByTestId("record-facts")).not.toHaveTextContent("Unclaimed");
    view.unmount();
    await agentView({ value: detail({ claim: null }) });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Unclaimed");
  });

  it("shows the Screening Agent as the control, and the Team member's name during a Takeover", async () => {
    const view = await agentView({ value: detail() });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Screening Agent");
    view.unmount();
    await agentView({ value: detail({ control: "human", control_user_id: "usr_rec_puebla", control_user_name: "Ana Torres" }) });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Takeover");
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Ana Torres");
  });

  it("shows the Locations and the Zone", async () => {
    await agentView({ value: detail() });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Sazón Puebla Centro");
    expect(screen.getByTestId("record-facts")).toHaveTextContent("Centro, Puebla");
  });

  it("shows the Interview, or that there is none yet", async () => {
    const view = await agentView({
      value: detail({
        interview: { starts_at: "2026-10-14T16:00:00Z", ends_at: "2026-10-14T16:30:00Z", local_label: "mié 14 · 10:00", location_name: "Sazón Puebla Centro" },
      }),
    });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("mié 14 · 10:00 at Sazón Puebla Centro");
    view.unmount();
    await agentView({ value: detail({ interview: null }) });
    expect(screen.getByTestId("record-facts")).toHaveTextContent("No Interview yet");
  });

  it("shows an outcome next to the stage", async () => {
    await agentView({ value: detail({ stage: "driver_license", outcome: "knockout", status: "closed" }) });
    expect(screen.getByTestId("record-stage")).toHaveTextContent("Driver's license");
    expect(screen.getByTestId("record-stage")).toHaveTextContent("Knockout");
  });
});

describe("record pane: live update", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  const zonePending = () => {
    const base = detail();
    return detail({ answers: { ...base.answers, zone: null }, answer_status: base.answer_status.filter((a) => a.field !== "zone") });
  };

  it("subscribes to this Application's stream, and each event or open refetches once, in place", async () => {
    const current = { value: zonePending() as ApplicationDetail };
    const { mock } = await agentView(current);
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe(`${DETAIL_PATH}/stream`);
    expect(detailCalls(mock)).toBe(1);
    expect(within(row("zone")).getByText("Pending")).toBeInTheDocument();

    current.value = detail();
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(within(row("zone")).getByRole("img", { name: "Valid" })).toBeInTheDocument());
    expect(detailCalls(mock)).toBe(2);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    act(() => source.emit("message", { type: "message", data: {} }));
    await waitFor(() => expect(detailCalls(mock)).toBe(3));
    act(() => source.emit("control", { type: "control", data: {} }));
    await waitFor(() => expect(detailCalls(mock)).toBe(4));
    act(() => source.emit("open"));
    await waitFor(() => expect(detailCalls(mock)).toBe(5));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Luis Hernández" })).toBeInTheDocument();
  });

  it("asks the sim clock again on each stream message or event, so the strip keeps up with the thread", async () => {
    mockApi({
      "GET /api/sim/clock": clockState,
      [`GET ${DETAIL_PATH}`]: () => detail(),
    });
    const clockCalls = () => (fetch as unknown as { mock: { calls: [RequestInfo | URL][] } }).mock.calls.filter(([url]) => String(url) === "/api/sim/clock").length;
    render(
      <QueryClientProvider client={newClient()}>
        <ClockStrip />
        <RecordPane lookup={lookupOf()} />
      </QueryClientProvider>,
    );
    await screen.findByRole("region", { name: "Simulated clock" });
    await screen.findByRole("heading", { name: "Luis Hernández" });
    const source = FakeEventSource.instances[0];
    await waitFor(() => expect(clockCalls()).toBe(1));
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(clockCalls()).toBe(2));
    act(() => source.emit("message", { type: "message", data: {} }));
    await waitFor(() => expect(clockCalls()).toBe(3));
  });

  it("closes the stream on unmount, and the old one when the id changes", async () => {
    const current = { value: detail() };
    const view = await agentView(current);
    const first = FakeEventSource.instances[0];
    mockApi({
      [`GET ${DETAIL_PATH}`]: () => current.value,
      "GET /api/applications/app_other": () => detail({ id: "app_other" }),
    });
    view.rerenderWith({ application: { ...APP, id: "app_other" } });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances[1].url).toBe("/api/applications/app_other/stream");
    expect(FakeEventSource.instances[1].closed).toBe(false);
    view.unmount();
    expect(FakeEventSource.instances[1].closed).toBe(true);
  });

  it("marks only the row whose value or status changed to settle, and nothing on an identical refetch", async () => {
    const current = { value: zonePending() as ApplicationDetail };
    const { mock } = await agentView(current);
    const source = FakeEventSource.instances[0];
    expect(settled()).toHaveLength(0);

    // Identical refetch: nothing settles.
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(detailCalls(mock)).toBe(2));
    expect(settled()).toHaveLength(0);

    // Zone gets its value and status: only that row settles.
    current.value = detail();
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(within(row("zone")).getByRole("img", { name: "Valid" })).toBeInTheDocument());
    expect(settled()).toEqual([row("zone")]);
    expect(row("zone").getAttribute("data-settle")).toMatch(/^[ab]$/);
  });

  it("plays the settle once: the marker leaves when its animation ends, and a second change plays again", async () => {
    const current = { value: zonePending() as ApplicationDetail };
    await agentView(current);
    const source = FakeEventSource.instances[0];
    current.value = detail();
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(settled()).toHaveLength(1));
    const first = row("zone").getAttribute("data-settle");
    endAnimation(row("zone"));
    expect(settled()).toHaveLength(0);

    const next = detail();
    current.value = { ...next, answers: { ...next.answers, zone: { id: "zone_mx_puebla_la_paz", name: "La Paz", city: "Puebla", country: "MX" } } };
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(settled()).toHaveLength(1));
    expect(row("zone").getAttribute("data-settle")).not.toBe(first);
  });

  it("marks the stage badge when the stage or the outcome changes", async () => {
    const current = { value: detail({ stage: "driver_license" }) as ApplicationDetail };
    await agentView(current);
    const source = FakeEventSource.instances[0];
    expect(screen.getByTestId("record-stage")).not.toHaveAttribute("data-settle");
    current.value = detail({ stage: "qualified" });
    act(() => source.emit("event", { type: "event", data: {} }));
    await waitFor(() => expect(screen.getByTestId("record-stage")).toHaveAttribute("data-settle"));
  });
});

describe("record pane: two views, no cost", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  const switchToRecruiter = async () => {
    const [agentTab] = screen.getAllByRole("tab");
    agentTab.focus();
    fireEvent.keyDown(agentTab, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Recruiter's view" })).toHaveAttribute("aria-selected", "true"));
  };

  const costText = ["0.0031", "86000", "2100", "61000", "7200", "1700", "haiku", "anthropic", "tokens", "est_cost", "reconciled"];

  function expectNoCost() {
    const text = document.body.textContent ?? "";
    const html = document.body.innerHTML;
    for (const word of costText) {
      expect(text).not.toContain(word);
      expect(html).not.toContain(word);
    }
    expect(text).not.toMatch(/\$\d/);
  }

  it("shows the Agent's view first, the Recruiter's view second, in a tab list", async () => {
    await agentView({ value: detail() });
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Agent's view", "Recruiter's view"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tablist", { name: "Record views" })).toBeInTheDocument();
  });

  it("shows no cost, token or model figure in either view, in text or in attributes", async () => {
    // The fixture's Operator-only cost is in the response; neither view may show it.
    expect(detail().cost?.est_cost_usd).toBe(0.0031);
    await agentView({ value: detail() });
    expectNoCost();
    await switchToRecruiter();
    expectNoCost();
  });

  it("shows the Candidate's own words, the attempts and the events in the Agent's view", async () => {
    await agentView({ value: detail() });
    expect(within(row("zone")).getByText(/Vivo en Centro, Puebla/)).toBeInTheDocument();
    expect(within(row("driver_license")).getByText(/sí, tengo licencia/)).toBeInTheDocument();
    expect(within(row("start_date")).getByText(/2 attempts/)).toBeInTheDocument();
    const events = screen.getByRole("list", { name: "Latest events" });
    expect(within(events).getByText("Application started")).toBeInTheDocument();
    expect(within(events).getByText("Screening finished")).toBeInTheDocument();
    expect(screen.queryByText(/earlier/)).not.toBeInTheDocument();
  });

  it("shows at most the latest five events, with how many are earlier", async () => {
    const events = Array.from({ length: 8 }, (_, i) => ({
      id: i + 1,
      kind: "answer_recorded" as const,
      actor: "agent" as const,
      user_name: null,
      data: {},
      created_at: `2026-10-09T10:0${i}:00Z`,
    }));
    await agentView({ value: detail({ events }) });
    const list = screen.getByRole("list", { name: "Latest events" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("and 3 earlier")).toBeInTheDocument();
    // The five shown are the latest five, oldest of them first.
    // Times are in the Candidate's zone (Mexico City), the same as the bubbles: 10:03Z is 04:03.
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("04:03");
    expect(within(list).getAllByRole("listitem")[4]).toHaveTextContent("04:07");
    expect(list).not.toHaveTextContent("UTC");
    // The count of earlier events sits before the list, so it reads as "3 earlier, then these five".
    expect(list.compareDocumentPosition(screen.getByText("and 3 earlier")) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it("shows only HH:mm for an event on the clock's day, and the date for any other day", async () => {
    const events = [
      { id: 1, kind: "application_started" as const, actor: "candidate" as const, user_name: null, data: {}, created_at: "2026-10-08T10:00:00Z" },
      { id: 2, kind: "answer_recorded" as const, actor: "agent" as const, user_name: null, data: {}, created_at: "2026-10-09T10:03:00Z" },
    ];
    const current = { value: detail({ events, claim: claimByAna }) as ApplicationDetail };
    mockApi({ [`GET ${DETAIL_PATH}`]: () => current.value });
    render(
      <QueryClientProvider client={newClient()}>
        <RecordPane lookup={lookupOf()} now={new Date("2026-10-09T16:00:00Z")} />
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "Luis Hernández" });
    const [older, today] = within(screen.getByRole("list", { name: "Latest events" })).getAllByRole("listitem");
    expect(older).toHaveTextContent("08 Oct, 04:00");
    expect(today).toHaveTextContent("04:03");
    expect(today).not.toHaveTextContent("Oct");
  });

  it("takes the clock's day from the stage's clock: events read HH:mm when the strip is on the same day", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/sim/clock": clockState,
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 1, agent_typing: false, run_finished: true, messages: [msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" })] }),
      "GET /api/applications": (call) => {
        const base = summary();
        return { items: [{ ...base, candidate: { ...base.candidate, phone: call.query.get("q") } }], next_cursor: null, total: 1 };
      },
      [`GET ${DETAIL_PATH}`]: () => detail(),
    });
    renderRoute("/stage");
    fireEvent.click(await screen.findByRole("button", { name: "Start conversation" }));
    await screen.findByRole("log", { name: "Messages" });
    const list = await screen.findByRole("list", { name: "Latest events" });
    expect(list).toHaveTextContent("04:00");
    expect(list).not.toHaveTextContent("Oct");
  });

  it("shows event times in Madrid for a Spanish Candidate", async () => {
    const base = detail();
    await agentView({
      value: detail({
        candidate: { ...base.candidate, country: "ES", phone: "+34612345678" },
        events: [{ id: 1, kind: "application_started", actor: "candidate", user_name: null, data: {}, created_at: "2026-10-09T10:00:00Z" }],
      }),
    });
    expect(within(screen.getByRole("list", { name: "Latest events" })).getByRole("listitem")).toHaveTextContent("12:00");
  });

  it("names the Stage an event moved to", async () => {
    await agentView({
      value: detail({
        events: [{ id: 1, kind: "stage_changed", actor: "agent", user_name: null, data: { from: "zone", to: "driver_license" }, created_at: "2026-10-09T10:00:00Z" }],
      }),
    });
    expect(within(screen.getByRole("list", { name: "Latest events" })).getByRole("listitem")).toHaveTextContent("Stage changed → Driver's license");
  });

  it("shows the Candidate's own words only when they say more than the value", async () => {
    const base = detail();
    await agentView({
      value: detail({
        answer_status: base.answer_status.map((a) =>
          a.field === "zone" ? { ...a, raw_input: "centro, PUEBLA" } : a.field === "full_name" ? { ...a, raw_input: "luis hernandez" } : a,
        ),
      }),
    });
    // Same as the value up to case and accents: not repeated.
    expect(within(row("zone")).queryByText(/PUEBLA/)).not.toBeInTheDocument();
    expect(within(row("full_name")).queryByText(/luis hernandez/)).not.toBeInTheDocument();
    // Different words stay.
    expect(within(row("driver_license")).getByText(/sí, tengo licencia/)).toBeInTheDocument();
  });

  it("puts the stage and outcome badge beside the Candidate's name", async () => {
    await agentView({ value: detail() });
    const name = screen.getByRole("heading", { name: "Luis Hernández" });
    expect(name.parentElement).toContainElement(screen.getByTestId("record-stage"));
  });

  it("shows the summary, the open approval and the Interview in the Recruiter's view, with no words of the Candidate, no events and no button", async () => {
    await agentView({
      value: detail({
        interview: { starts_at: "2026-10-14T16:00:00Z", ends_at: "2026-10-14T16:30:00Z", local_label: "mié 14 · 10:00", location_name: "Sazón Puebla Centro" },
      }),
    });
    await switchToRecruiter();
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Luis lives in central Puebla and holds a valid licence.");
    expect(within(panel).getByRole("list", { name: "Waiting for a person" })).toHaveTextContent("Approval");
    expect(panel).toHaveTextContent("mié 14 · 10:00 at Sazón Puebla Centro");
    expect(within(panel).getAllByRole("img", { name: "Valid" }).length).toBeGreaterThan(0);
    expect(panel).not.toHaveTextContent("Vivo en Centro, Puebla");
    expect(panel).not.toHaveTextContent("Application started");
    expect(within(panel).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Latest events" })).not.toBeInTheDocument();
  });

  it("lays the Recruiter's view out differently: Summary full width under the facts, a compact two-column Answers, no split and no Candidate words", async () => {
    await agentView({ value: detail() });
    // The Agent's view has the split, the words and no Summary.
    expect(document.querySelector(".record-split")).not.toBeNull();
    expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("“");
    await switchToRecruiter();
    const panel = screen.getByRole("tabpanel");
    const summary = within(panel).getByRole("region", { name: "Summary" });
    expect(summary.parentElement).toBe(panel);
    expect(panel.querySelector(".record-split")).toBeNull();
    const answers = within(panel).getByRole("region", { name: "Answers" });
    expect(answers.parentElement).toBe(panel);
    expect(answers.querySelector(".record-answers--compact")).not.toBeNull();
    expect(panel).not.toHaveTextContent("“");
    expect(within(panel).queryByText(/tengo licencia/)).not.toBeInTheDocument();
    // Order: facts, then what waits for a person (it leads), then the Summary, then the Answers.
    const order = [screen.getByTestId("record-facts"), within(panel).getByRole("list", { name: "Waiting for a person" }), summary, answers];
    for (let i = 1; i < order.length; i += 1) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("leads the Recruiter's view with the open approval: the first element after the facts, in the live treatment", async () => {
    await agentView({ value: detail() });
    await switchToRecruiter();
    const facts = screen.getByTestId("record-facts");
    const lead = facts.nextElementSibling as HTMLElement;
    expect(lead).toBe(within(screen.getByRole("tabpanel")).getByRole("list", { name: "Waiting for a person" }));
    expect(lead).toHaveClass("record-queue--lead");
    expect(lead.querySelector(".ui-tag--live")).toHaveTextContent("Approval");
    expect(lead.querySelector(".record-queue__text")).toHaveTextContent("Waiting for approval since");
    // The Summary comes after it.
    expect(lead.nextElementSibling).toBe(screen.getByRole("region", { name: "Summary" }));
  });

  it("leaves the Agent's view without the lead: no waiting line, facts then the split", async () => {
    await agentView({ value: detail() });
    expect(screen.queryByRole("list", { name: "Waiting for a person" })).not.toBeInTheDocument();
    expect(screen.getByTestId("record-facts").nextElementSibling).toHaveClass("record-split");
  });

  it("says what waits for a person, since when, with the live Tag, in the Recruiter's view", async () => {
    mockApi({ [`GET ${DETAIL_PATH}`]: () => detail() });
    render(
      <QueryClientProvider client={newClient()}>
        <RecordPane lookup={lookupOf()} now={new Date("2026-10-09T16:00:00Z")} />
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "Luis Hernández" });
    await switchToRecruiter();
    const item = within(screen.getByRole("list", { name: "Waiting for a person" })).getByRole("listitem");
    expect(item).toHaveTextContent("Waiting for approval since 04:04");
    expect(item.querySelector(".ui-tag--live")).toHaveTextContent("Approval");
  });

  it("shows the Recruiter's view without a summary or an approval when there are none", async () => {
    await agentView({ value: detail({ summary: null, open_queue_items: [] }) });
    await switchToRecruiter();
    const panel = screen.getByRole("tabpanel");
    expect(panel).not.toHaveTextContent("Luis lives in central Puebla");
    expect(within(panel).queryByRole("list", { name: "Waiting for a person" })).not.toBeInTheDocument();
  });

  it("shows the flags of the Application in the Recruiter's view", async () => {
    await agentView({ value: detail({ flags: ["qualified_waiting"] }) });
    await switchToRecruiter();
    expect(within(screen.getByRole("tabpanel")).getByText("Qualified, waiting")).toBeInTheDocument();
  });

  it("cannot be given a cost by type: the detail it holds omits cost", () => {
    expectTypeOf<StageDetail>().not.toHaveProperty("cost");
    expectTypeOf<Parameters<typeof RecordPane>[0]>().not.toHaveProperty("cost");
    expectTypeOf<ApplicationDetail>().toHaveProperty("cost");
  });

  it("shows why the Summary has no written part in the Recruiter's view: retrying, then failed", async () => {
    const base = detail();
    const withState = (state: string): ApplicationDetail =>
      detail({ summary: { ...base.summary!, prose: null, structured: { ...base.summary!.structured, prose_status: state } } });
    const current = { value: withState("retrying") };
    await agentView(current);
    await switchToRecruiter();
    const summary = within(screen.getByRole("tabpanel")).getByRole("region", { name: "Summary" });
    expect(within(summary).getByRole("status")).toHaveTextContent("The written summary is not ready yet. It is retried automatically");
    cleanup();
    await agentView({ value: withState("failed") });
    await switchToRecruiter();
    expect(within(screen.getByRole("tabpanel")).getByRole("status")).toHaveTextContent("The written summary could not be produced.");
  });

  it("shows no Summary in the Recruiter's view when no written part was asked for", async () => {
    const base = detail();
    await agentView({ value: detail({ summary: { ...base.summary!, prose: null } }) });
    await switchToRecruiter();
    expect(within(screen.getByRole("tabpanel")).queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
  });
});

describe("stage sources", () => {
  const dir = join(__dirname, "../../src/stage");
  const sources = readdirSync(dir).filter((name) => /\.tsx?$/.test(name));

  it("imports nothing from the recruiter pages or the dashboards", () => {
    expect(sources.length).toBeGreaterThan(5);
    for (const name of sources) {
      const text = readFileSync(join(dir, name), "utf8");
      const imports = [...text.matchAll(/from\s+["']([^"']+)["']/g)].map((m) => m[1]);
      for (const path of imports) {
        expect(path, `${name} imports ${path}`).not.toMatch(/pages\/ApplicationDetail|components\/TakeoverPanel|components\/Transcript|dashboards\//);
      }
    }
  });

  it("imports primitives by file, never through the ui index, and never StageScreen statically outside itself", () => {
    for (const name of sources) {
      const text = readFileSync(join(dir, name), "utf8");
      expect(text, name).not.toMatch(/components\/ui\/index/);
      expect(text, name).not.toMatch(/MotionProvider|MotionConfig/);
      if (name !== "StageGate.tsx") expect(text, name).not.toMatch(/from\s+["']\.\/StageScreen/);
    }
    expect(readFileSync(join(dir, "StageGate.tsx"), "utf8")).not.toMatch(/^import .*StageScreen/m);
  });
});

// ---------------------------------------------------------------------------------------------
// Look: motion and tokens in stage.css
// ---------------------------------------------------------------------------------------------

describe("stage.css", () => {
  const css = readFileSync(join(__dirname, "../../src/styles/surfaces/stage.css"), "utf8");

  it("turns every animation and transition in the stage off under prefers-reduced-motion", () => {
    const block = css.slice(css.lastIndexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block).toMatch(/\.stage[^{]*\*[^{]*\{[^}]*animation:\s*none/);
    expect(block).toMatch(/transition-duration:\s*0s/);
  });

  it("beats the specificity of every selector that starts a stage animation", () => {
    const block = css.slice(css.lastIndexOf("@media (prefers-reduced-motion: reduce)"));
    // `.record [data-settle]` and `.phone-typing span` are more specific than `.stage *`, so they are listed.
    expect(block).toContain(".stage .record [data-settle]");
    expect(block).toContain(".stage .phone-typing span");
    const stageCss = css
      .slice(css.indexOf("Demo stage (/stage)"), css.lastIndexOf("@media (prefers-reduced-motion: reduce)"))
      .replace(/\/\*[\s\S]*?\*\//g, "");
    const animated = [...stageCss.matchAll(/([^{}]+)\{[^{}]*\banimation:/g)].map((m) => m[1].trim().replace(/\s+/g, " "));
    expect(animated.length).toBeGreaterThan(3);
    for (const selector of animated) {
      expect(selector, selector).toMatch(/^(\.phone-msg|\.phone-typing span|\.phone-sheet|\.record \[data-settle="[ab]"\])$/);
    }
  });

  it("takes no space for an inactive tab panel: the flex rule on the panel is followed by [hidden] { display: none }", () => {
    const flexRule = css.indexOf(".record .ui-tabs__panel {");
    const hiddenRule = css.search(/\.record \.ui-tabs__panel\[hidden\]\s*\{[^}]*display:\s*none/);
    expect(flexRule).toBeGreaterThan(-1);
    expect(hiddenRule).toBeGreaterThan(flexRule);
  });

  it("lets no card stretch: every card in the split hugs its content", () => {
    expect(css).toMatch(/\.record-stack\s*\{[^}]*align-content:\s*start/);
    expect(css).toMatch(/\.record-split > \.ui-card\s*\{[^}]*height:\s*auto/);
    expect(css).not.toMatch(/\.record-split > \.ui-card\s*\{[^}]*height:\s*100%/);
  });

  it("sets the Recruiter's answers in two columns, the earlier-events note in the text face, and the ghost still", () => {
    expect(css).toMatch(/\.record-answers--compact\s*\{[^}]*grid-template-columns:\s*repeat\(2/);
    const earlier = css.match(/\.record-earlier\s*\{[^}]*\}/)?.[0] ?? "";
    expect(earlier).not.toMatch(/--mono/);
    const ghost = css.slice(css.indexOf(".record-ghost"), css.indexOf(".record-head {"));
    expect(ghost).not.toMatch(/animation/);
  });

  it("sets the leading approval in the live tokens at title size: --live-soft wash, --live-edge border, --ink-strong text", () => {
    const lead = css.match(/\.record-queue__item\s*\{[^}]*\}/)?.[0] ?? "";
    expect(lead).toMatch(/var\(--live-soft\)/);
    expect(lead).toMatch(/var\(--live-edge\)/);
    expect(lead).not.toMatch(/#|rgb/);
    const text = css.match(/\.record-queue--lead \.record-queue__text\s*\{[^}]*\}/)?.[0] ?? "";
    expect(text).toMatch(/var\(--text-lg\)/);
    expect(text).toMatch(/var\(--ink-strong\)/);
  });

  it("gives the list footer a muted, borderless spent state", () => {
    const spent = css.match(/\.phone-list:disabled\s*\{[^}]*\}/)?.[0] ?? "";
    expect(spent).toMatch(/var\(--ink-muted\)/);
    expect(spent).not.toMatch(/opacity|dashed/);
  });

  it("centres the record's empty state on the start card", () => {
    expect(css).toMatch(/\.record-placeholder\s*\{[^}]*justify-content:\s*center/);
  });

  it("shows a spent reply as a solid, muted button attached under its bubble, not a dashed placeholder", () => {
    const rule = css.match(/\.phone-reply:disabled\s*\{[^}]*\}/)?.[0] ?? "";
    expect(rule).not.toMatch(/dashed/);
    expect(rule).toMatch(/var\(--ink-muted\)/);
    expect(css).toMatch(/\.phone-msg:has\(\.phone-replies\) \.phone-bubble\s*\{[^}]*border-end-start-radius:\s*0/);
  });

  it("shows a disabled reply by colour and border, never by opacity", () => {
    const rule = css.match(/\.phone-reply:disabled\s*\{[^}]*\}/);
    expect(rule).not.toBeNull();
    expect(rule?.[0]).not.toMatch(/opacity/);
    expect(rule?.[0]).toMatch(/border-color/);
    expect(rule?.[0]).toMatch(/color:/);
  });

  it("sets the phone's body text at --text-md and the record's values a step larger", () => {
    expect(css).toMatch(/\.phone\s*\{[^}]*var\(--text-md\)/);
    expect(css).toMatch(/\.record-row__value\s*\{[^}]*var\(--text-lg\)/);
  });

  it("keeps shell-app's .testchat rules", () => {
    expect(css).toContain(".testchat .bubble.typing");
    expect(css).toContain("@keyframes testchat-bubble-in");
  });

  it("plays each stage animation once, never in a loop", () => {
    const stageCss = css.slice(css.indexOf("Demo stage (/stage)"));
    expect(stageCss).not.toMatch(/infinite/);
  });
});

// ---------------------------------------------------------------------------------------------
// Presenter console: the recording window, the takes list and the shortcuts (SC1, SC3, SC4)
// ---------------------------------------------------------------------------------------------

describe("recordingWindow", () => {
  // Mexico City is UTC-6 all year since October 2022, so 08:59 there is 14:59 UTC.
  const at = (utc: string) => recordingWindow(new Date(`2026-10-12T${utc}Z`));

  it("is inclusive at 09:00 and exclusive at 22:00 Mexico City", () => {
    expect(RECORDING_WINDOW).toMatchObject({ zone: "America/Mexico_City", startHour: 9, endHour: 22 });
    expect(at("14:59:00").inside).toBe(false);
    expect(at("15:00:00").inside).toBe(true);
    expect(recordingWindow(new Date("2026-10-13T03:59:00Z")).inside).toBe(true); // 21:59 Mexico City
    expect(recordingWindow(new Date("2026-10-13T04:00:00Z")).inside).toBe(false); // 22:00 Mexico City
  });

  it("reads the clock time in Mexico City", () => {
    expect(at("14:59:00").time).toBe("08:59");
    expect(at("15:00:00").time).toBe("09:00");
    expect(recordingWindow(new Date("2026-10-13T03:59:00Z")).time).toBe("21:59");
    expect(recordingWindow(new Date("2026-10-13T06:05:00Z")).time).toBe("00:05"); // h23, never 24:05
  });

  it("does not depend on the browser's own zone", () => {
    const before = process.env.TZ;
    try {
      for (const zone of ["Asia/Tokyo", "Pacific/Auckland", "America/Los_Angeles"]) {
        process.env.TZ = zone;
        // The host zone really is not Mexico City: the same instant reads another hour here.
        expect(new Date("2026-10-12T15:00:00Z").getHours()).not.toBe(9);
        expect(recordingWindow(new Date("2026-10-12T14:59:00Z"))).toMatchObject({ inside: false, time: "08:59" });
        expect(recordingWindow(new Date("2026-10-12T15:00:00Z"))).toMatchObject({ inside: true, time: "09:00" });
        expect(recordingWindow(new Date("2026-10-13T03:59:00Z"))).toMatchObject({ inside: true, time: "21:59" });
        expect(recordingWindow(new Date("2026-10-13T04:00:00Z"))).toMatchObject({ inside: false, time: "22:00" });
      }
    } finally {
      if (before === undefined) delete process.env.TZ;
      else process.env.TZ = before;
    }
  });
});

describe("takes", () => {
  const take = (n: number, over: Partial<Take> = {}): Take => ({
    conversationId: `wc_${String(n).padStart(32, "0")}`,
    phone: `+5255${String(10000000 + n)}`,
    personaId: "no_license_mx",
    country: "MX",
    locale: "es-MX",
    startedAt: `2026-10-12T15:${String(n % 60).padStart(2, "0")}:00Z`,
    ...over,
  });
  const stored = () => JSON.parse(window.sessionStorage.getItem(TAKES_KEY) ?? "null") as Record<string, unknown>[] | null;

  it("keeps the list under criba.stage.takes", () => {
    expect(TAKES_KEY).toBe("criba.stage.takes");
    expect(MAX_TAKES).toBe(20);
  });

  it("lists the newest first and stores exactly the six keys, no message text and no name", async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    expect(readTakes()).toEqual([]);
    // A caller that hands over more than a Take (a name, a message) must not get it stored.
    const loose = { ...take(1), name: "Ana García", text: "Hola, soy Ana" } as Take;
    addTake(loose);
    addTake(take(2));
    expect(readTakes().map((t) => t.conversationId)).toEqual([take(2).conversationId, take(1).conversationId]);
    const rows = stored() ?? [];
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(Object.keys(row).sort()).toEqual(["conversationId", "country", "locale", "personaId", "phone", "startedAt"]);
    }
    const raw = window.sessionStorage.getItem(TAKES_KEY) ?? "";
    expect(raw).not.toContain("Ana");
    expect(raw).not.toContain("Hola");
  });

  it("holds one row per conversation id, the latest add winning and moving to the front", async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    addTake(take(1));
    addTake(take(2));
    addTake(take(1, { personaId: "confirm_correction_mx" }));
    const list = readTakes();
    expect(list.map((t) => t.conversationId)).toEqual([take(1).conversationId, take(2).conversationId]);
    expect(list[0].personaId).toBe("confirm_correction_mx");
  });

  it("keeps at most 20, dropping the oldest", async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    for (let n = 1; n <= 23; n += 1) addTake(take(n));
    const list = readTakes();
    expect(list).toHaveLength(20);
    expect(list[0].conversationId).toBe(take(23).conversationId);
    expect(list[19].conversationId).toBe(take(4).conversationId);
    expect(stored()).toHaveLength(20);
  });

  it("ignores stored rows that are not takes", async () => {
    window.sessionStorage.setItem(TAKES_KEY, JSON.stringify([take(1), { conversationId: 7 }, "x", null, { ...take(2), phone: undefined }]));
    const { readTakes } = await import("../../src/stage/takes.ts");
    expect(readTakes().map((t) => t.conversationId)).toEqual([take(1).conversationId]);
    window.sessionStorage.setItem(TAKES_KEY, "{not json");
    expect(readTakes()).toEqual([]);
  });

  // takes.ts keeps a copy in memory for when storage fails, and a flag that storage is behind it. Both live in
  // the module, which these tests share with the console tests after them (a module registry reset would make
  // every later lazy route load a second copy of the auth context). So each of these tests starts and ends
  // from a known state, reached through the public functions only: one write that storage accepts clears the
  // flag, and a read of the emptied storage clears the copy.
  const resetTakes = async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    addTake(take(0));
    window.sessionStorage.clear();
    expect(readTakes()).toEqual([]);
  };
  beforeEach(resetTakes);
  afterEach(resetTakes);

  it("keeps working in memory when sessionStorage getItem and setItem throw", async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    try {
      expect(readTakes()).toEqual([]);
      expect(() => addTake(take(1))).not.toThrow();
      expect(() => addTake(take(2))).not.toThrow();
      expect(readTakes().map((t) => t.conversationId)).toEqual([take(2).conversationId, take(1).conversationId]);
      expect(get).toHaveBeenCalled();
      expect(set).toHaveBeenCalled();
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });

  it("keeps working when only setItem throws: the next read does not wipe what storage refused", async () => {
    const { addTake, readTakes } = await import("../../src/stage/takes.ts");
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    try {
      addTake(take(3));
      addTake(take(4));
      expect(window.sessionStorage.getItem(TAKES_KEY)).toBeNull();
      expect(readTakes().map((t) => t.conversationId)).toEqual([take(4).conversationId, take(3).conversationId]);
    } finally {
      set.mockRestore();
    }
    // Once storage takes a write again, it is the truth again and holds the list.
    addTake(take(5));
    expect(stored()?.map((row) => row.conversationId)).toEqual([take(5).conversationId, take(4).conversationId, take(3).conversationId]);
  });

  it("starts each test from an empty list, whatever an earlier test left in memory", async () => {
    const { readTakes } = await import("../../src/stage/takes.ts");
    expect(readTakes()).toEqual([]);
    expect(window.sessionStorage.getItem(TAKES_KEY)).toBeNull();
  });
});

describe("presenter console shortcuts", () => {
  const key = (init: Partial<KeyboardEventInit> & { key: string }) => new KeyboardEvent("keydown", init);

  it("matches Ctrl+. for the console and Ctrl+, for playback", () => {
    expect(matchesShortcut(key({ key: ".", ctrlKey: true }), CONSOLE_SHORTCUT)).toBe(true);
    expect(matchesShortcut(key({ key: ",", ctrlKey: true }), PLAYBACK_SHORTCUT)).toBe(true);
    expect(CONSOLE_SHORTCUT).not.toEqual(PLAYBACK_SHORTCUT);
  });

  it("does not match the key without the modifier, nor with another one", () => {
    expect(matchesShortcut(key({ key: "." }), CONSOLE_SHORTCUT)).toBe(false);
    expect(matchesShortcut(key({ key: "," }), PLAYBACK_SHORTCUT)).toBe(false);
    expect(matchesShortcut(key({ key: ".", metaKey: true }), CONSOLE_SHORTCUT)).toBe(false);
    expect(matchesShortcut(key({ key: ".", altKey: true }), CONSOLE_SHORTCUT)).toBe(false);
    expect(matchesShortcut(key({ key: ".", ctrlKey: true, altKey: true }), CONSOLE_SHORTCUT)).toBe(false);
  });

  it("does not cross the two chords", () => {
    expect(matchesShortcut(key({ key: ",", ctrlKey: true }), CONSOLE_SHORTCUT)).toBe(false);
    expect(matchesShortcut(key({ key: ".", ctrlKey: true }), PLAYBACK_SHORTCUT)).toBe(false);
  });
});

describe("console copy", () => {
  it("lives in lib/labels/stage.ts and uses CONTEXT.md terms", async () => {
    const labels = await import("../../src/lib/labels/stage.ts");
    expect(labels.consoleTitle).toBe("Presenter console");
    expect(labels.windowInside).toBe("Inside the recording window");
    expect(labels.windowOutside).toBe("Outside the recording window (09:00 to 22:00)");
    expect(labels.takesEmpty).toBe("No takes yet. New take starts one.");
    expect(labels.resetConfirm).toMatch(/moves the clock back to real time/);
    expect(labels.costLine).toBe("About $0.01 per conversation");
  });

  it("keeps playerCopy.ts as a re-export of the same words", async () => {
    const labels = await import("../../src/lib/labels/stage.ts");
    const copy = await import("../../src/stage/playerCopy.ts");
    expect(copy.playerCopy.groupLabel).toBe(labels.playbackGroup);
    expect(copy.playerCopy.costLine).toBe(labels.costLine);
    expect(copy.playerCopy.play).toBe("Play");
    expect(copy.pauseReasonText("composer")).toBe("Paused: you typed in the composer");
    expect(copy.errorKindText("send")).toBe("Could not send the message");
    expect(copy.unavailableOption("x", null)).toBe("x (unavailable: not playable)");
    expect(copy.unavailableOption("x", "no reply")).toBe("x (unavailable: no reply)");
  });
});

// ---------------------------------------------------------------------------------------------
// Presenter console: the host, the hand-over of the player and the two chords (SC1, SC4)
// ---------------------------------------------------------------------------------------------

describe("presenter console host", () => {
  const CONSOLE = { name: "Presenter console" };
  const ctrl = (key: string, target: Element = document.activeElement ?? document.body) => fireEvent.keyDown(target, { key, ctrlKey: true });
  const consoleRegion = () => screen.queryByRole("region", CONSOLE);
  const status = () => screen.getByTestId("player-status").textContent;
  const stub = () =>
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/sim/clock": clockState,
      "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo" }],
      "GET /api/chat/conversations/:id/messages": { cursor: 0, agent_typing: false, run_finished: false, messages: [] },
      "GET /api/applications": { items: [], next_cursor: null, total: 0 },
    });
  const openStage = async () => {
    const mock = stub();
    const view = renderRoute("/stage");
    await screen.findByRole("region", { name: "Phone" });
    return { mock, ...view };
  };
  /** Opens the console, chooses a persona with a long pause so no message is sent, and closes it again. */
  const readyTake = async () => {
    ctrl(".");
    const region = await screen.findByRole("region", CONSOLE);
    fireEvent.change(within(region).getByRole("combobox", { name: "Persona" }), { target: { value: "no_license_mx" } });
    fireEvent.change(within(region).getByLabelText("Pause before each message (seconds)"), { target: { value: "30" } });
    await waitFor(() => expect(within(region).getByRole("button", { name: "Play" })).toBeEnabled());
    ctrl(".");
    await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());
  };

  beforeEach(() => {
    consoleLoads.renders = 0;
  });

  it("is not in the DOM while closed: no region, no heading, no Playback group, no text", async () => {
    await openStage();
    expect(consoleRegion()).not.toBeInTheDocument();
    expect(screen.queryByText("Presenter console")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Playback" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("player-status")).not.toBeInTheDocument();
  });

  it("opens on Ctrl+. as a non-modal region named Presenter console that holds the Playback group", async () => {
    await openStage();
    ctrl(".");
    const region = await screen.findByRole("region", CONSOLE);
    expect(region).not.toHaveAttribute("aria-modal");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(within(region).getByRole("heading", CONSOLE)).toBeInTheDocument();
    // One Playback group in the whole page, and it is the console's: never also on the stage.
    expect(screen.getAllByRole("group", { name: "Playback" })).toHaveLength(1);
    expect(within(region).getByRole("group", { name: "Playback" })).toBeInTheDocument();
    expect(consoleLoads.renders).toBeGreaterThan(0);
  });

  it("does not open on the bare key, on Ctrl+, or on Meta+.", async () => {
    await openStage();
    fireEvent.keyDown(document.body, { key: "." });
    fireEvent.keyDown(document.body, { key: ".", metaKey: true });
    ctrl(",");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(consoleRegion()).not.toBeInTheDocument();
  });

  it("closes on Ctrl+. and gives focus back to the element that had it", async () => {
    await openStage();
    const start = screen.getByRole("button", { name: "Start conversation" });
    start.focus();
    expect(start).toHaveFocus();
    ctrl(".");
    const region = await screen.findByRole("region", CONSOLE);
    expect(region).toContainElement(document.activeElement as HTMLElement);
    ctrl(".");
    await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());
    expect(start).toHaveFocus();
  });

  it("closes on Escape and gives focus back to the element that had it", async () => {
    await openStage();
    const start = screen.getByRole("button", { name: "Start conversation" });
    start.focus();
    ctrl(".");
    const region = await screen.findByRole("region", CONSOLE);
    fireEvent.keyDown(within(region).getByRole("combobox", { name: "Persona" }), { key: "Escape" });
    await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());
    expect(start).toHaveFocus();
  });

  it("answers Ctrl+. from a field too, because the chord carries a modifier", async () => {
    await openStage();
    const country = screen.getByLabelText("Country");
    country.focus();
    ctrl(".", country);
    expect(await screen.findByRole("region", CONSOLE)).toBeInTheDocument();
  });

  it("starts a ready take on Ctrl+, with the console closed, then pauses and resumes it", async () => {
    const { mock } = await openStage();
    await readyTake();
    expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(0);

    ctrl(",");
    await waitFor(() => expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(1));
    expect(consoleRegion()).not.toBeInTheDocument();

    ctrl(".");
    await screen.findByRole("region", CONSOLE);
    expect(status()).toBe("Running");
    ctrl(".");
    await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());

    ctrl(","); // pauses the running take
    ctrl(".");
    await screen.findByRole("region", CONSOLE);
    expect(status()).toBe("Paused");
    ctrl(".");
    await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());

    ctrl(","); // resumes it
    ctrl(".");
    await screen.findByRole("region", CONSOLE);
    expect(status()).toBe("Running");
    // One engine through all of it: a close and a reopen never started a second conversation.
    expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(1);
  });

  it("pauses and resumes on Ctrl+, with the console open as well", async () => {
    const { mock } = await openStage();
    await readyTake();
    ctrl(".");
    await screen.findByRole("region", CONSOLE);
    ctrl(",");
    await waitFor(() => expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(1));
    await waitFor(() => expect(status()).toBe("Running"));
    ctrl(",");
    await waitFor(() => expect(status()).toBe("Paused"));
    ctrl(",");
    await waitFor(() => expect(status()).toBe("Running"));
    expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(1);
  });

  it("does nothing on Ctrl+, before a persona is chosen", async () => {
    const { mock } = await openStage();
    ctrl(",");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(mock.find("POST", "/api/chat/conversations")).toHaveLength(0);
    expect(consoleRegion()).not.toBeInTheDocument();
  });

  it("registers one keydown listener for both chords, so one press is one toggle", async () => {
    const add = vi.spyOn(window, "addEventListener");
    try {
      await openStage();
      await waitFor(() => expect(consoleLoads.renders).toBeGreaterThan(0));
      await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
      ctrl(".");
      await screen.findByRole("region", CONSOLE);
      expect(add.mock.calls.filter(([type]) => type === "keydown")).toHaveLength(1);
      ctrl(".");
      await waitFor(() => expect(consoleRegion()).not.toBeInTheDocument());
    } finally {
      add.mockRestore();
    }
  });

  it("loads no console chunk and shows no console for a Recruiter, even on the chord", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    ctrl(".", document.body);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(consoleRegion()).not.toBeInTheDocument();
    expect(consoleLoads.renders).toBe(0);
  });

  it("loads no console chunk and shows no console for an Operator under Preview as Recruiter", async () => {
    previewOn();
    mockApi({ "GET /api/auth/me": operatorMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    ctrl(".", document.body);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
    expect(consoleRegion()).not.toBeInTheDocument();
    expect(consoleLoads.renders).toBe(0);
  });

  it("keeps the console slot's wrapper from costing a gap while the console is closed", () => {
    const css = readFileSync(join(__dirname, "../../src/styles/surfaces/stage.css"), "utf8");
    expect(css).toMatch(/\.stage-console\s*\{[^}]*display:\s*contents/);
    expect(css).toMatch(/\.stage-console__panel\s*\{[^}]*position:\s*fixed/);
  });
});

// ---------------------------------------------------------------------------------------------
// Presenter console: takes, Prepare take, reopen and the clock section (SC1, SC3, SC4)
// ---------------------------------------------------------------------------------------------

interface WorldConversation {
  id: string;
  phone: string;
  name: string;
  thread: ChatMessage[];
  outcome: "knockout" | null;
}

/**
 * An in-memory backend for the console: conversations that the test plays the agent in, an Application
 * for each Candidate who has written, and the sim clock. Anything the console asks of it is recorded by
 * mockApi, so a test can see what was sent and what was asked.
 */
function makeWorld(options: { sim?: boolean } = {}) {
  const sim = options.sim ?? true;
  const convs = new Map<string, WorldConversation>();
  const clock = { now: clockState.now };
  const flags = { listFails: false, listHangs: false };
  let counter = 0;
  const idOf = (path: string) => path.split("/")[4];
  const appOf = (conv: WorldConversation) => `app_${conv.id.slice(-4)}`;
  const state = () => ({ now: clock.now, offset_seconds: 0, due_jobs_run: 0 });
  const summaryOf = (conv: WorldConversation) => {
    const base = summary();
    return summary({
      id: appOf(conv),
      candidate: { ...base.candidate, phone: conv.phone, full_name: conv.name },
      outcome: conv.outcome,
      status: conv.outcome ? "closed" : "open",
    });
  };
  const seed = (id: string, phone: string, name: string, thread: ChatMessage[] = []) => {
    const conv: WorldConversation = { id, phone, name, thread, outcome: null };
    convs.set(id, conv);
    return conv;
  };
  const nextId = (conv: WorldConversation) => (conv.thread.at(-1)?.id ?? 0) + 1;
  const say = (id: string, ...texts: string[]) => {
    const conv = convs.get(id) as WorldConversation;
    for (const text of texts) conv.thread.push(msg({ id: nextId(conv), direction: "outbound", sender: "agent", sender_label: "Sazón Empleo", text }));
  };
  const latest = () => [...convs.values()].at(-1) as WorldConversation;

  const table = {
    "GET /api/auth/me": operatorMe,
    "POST /api/chat/conversations": (call: { body: unknown }) => {
      counter += 1;
      const id = `wc_${String(counter).padStart(32, "0")}`;
      seed(id, (call.body as { phone: string }).phone, `Candidato ${counter}`);
      return [201, { conversation_id: id, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo" }];
    },
    "GET /api/chat/conversations/:id/messages": (call: { path: string }) => {
      const thread = convs.get(idOf(call.path))?.thread ?? [];
      return { cursor: thread.at(-1)?.id ?? 0, agent_typing: false, run_finished: thread.at(-1)?.direction !== "inbound", messages: thread };
    },
    "POST /api/chat/conversations/:id/messages": (call: { path: string; body: unknown }) => {
      const conv = convs.get(idOf(call.path)) as WorldConversation;
      const id = nextId(conv);
      conv.thread.push(msg({ id, direction: "inbound", sender: "candidate", text: (call.body as { text: string }).text }));
      return [202, { message_id: `m${id}`, id, duplicate: false }];
    },
    "GET /api/applications": (call: { query: URLSearchParams }) => {
      if (flags.listHangs) return new Promise<object>(() => undefined);
      if (flags.listFails) return error(500, "internal", "boom");
      const phone = call.query.get("q");
      const conv = [...convs.values()].find((c) => c.phone === phone && c.thread.some((m) => m.direction === "inbound"));
      // A decoy whose phone only starts like the one asked for: the exact match must win.
      const decoy = summary({ id: "app_decoy", candidate: { ...summary().candidate, phone: `${phone}9`, full_name: "Decoy" }, outcome: "knockout" });
      return { items: conv ? [decoy, summaryOf(conv)] : [decoy], next_cursor: null, total: 1 };
    },
    "GET /api/applications/:id": (call: { path: string }) => {
      const conv = [...convs.values()].find((c) => appOf(c) === call.path.split("/")[3]);
      return conv ? detail({ ...summaryOf(conv) }) : detail();
    },
    "GET /api/sim/clock": () => (sim ? state() : error(404, "sim_disabled", "No simulated clock.")),
    "POST /api/sim/clock/advance": (call: { body: unknown }) => {
      clock.now = new Date(Date.parse(clock.now) + (call.body as { seconds: number }).seconds * 1000).toISOString();
      return state();
    },
    "POST /api/sim/clock/reset": () => {
      clock.now = clockState.now;
      return state();
    },
  };
  const mock = mockApi(table as Parameters<typeof mockApi>[0]);
  return { mock, convs, flags, clock, seed, say, latest };
}

function seedTakes(takes: Take[]) {
  window.sessionStorage.setItem(TAKES_KEY, JSON.stringify(takes));
}

const takeRow = (n: number, over: Partial<Take> = {}): Take => ({
  conversationId: `wc_${String(n).padStart(32, "0")}`,
  phone: `+52551234000${n}`,
  personaId: "confirm_correction_mx",
  country: "MX",
  locale: "es-MX",
  startedAt: `2026-10-09T10:0${n}:00Z`,
  ...over,
});

describe("presenter console: takes, Prepare take, reopen and the clock", () => {
  const CONSOLE = { name: "Presenter console" };
  const ctrl = (key: string) => fireEvent.keyDown(document.activeElement ?? document.body, { key, ctrlKey: true });
  const tick = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  /** Lets the agent's reply be read (the hook polls every 1.5 s) and answered, ticking until `check` holds. */
  const until = async (check: () => void) => {
    let last: unknown;
    for (let i = 0; i < 30; i += 1) {
      await tick(300);
      try {
        check();
        return;
      } catch (e) {
        last = e;
      }
    }
    throw last;
  };
  const openConsole = async () => {
    ctrl(".");
    return screen.findByRole("region", CONSOLE);
  };
  const stageUp = async () => {
    const view = renderRoute("/stage");
    await screen.findByRole("region", { name: "Phone" });
    return view;
  };
  const picker = (region: HTMLElement) => within(region).getByRole("combobox", { name: "Persona" }) as HTMLSelectElement;
  const choose = (region: HTMLElement, id: string) => fireEvent.change(picker(region), { target: { value: id } });
  const press = async (region: HTMLElement, name: string) => {
    fireEvent.click(within(region).getByRole("button", { name }));
    await tick(0);
  };
  const setPace = (region: HTMLElement, speed: string, pause: string) => {
    fireEvent.change(within(region).getByLabelText("Characters per second"), { target: { value: speed } });
    fireEvent.change(within(region).getByLabelText("Pause before each message (seconds)"), { target: { value: pause } });
  };
  const statusOf = (region: HTMLElement) => within(region).getByTestId("player-status").textContent;
  const takesTable = (region: HTMLElement) => within(region).getByRole("table", { name: "Takes" });
  const rowsOf = (region: HTMLElement) => within(takesTable(region)).getAllByRole("row").slice(1);
  const posts = (world: ReturnType<typeof makeWorld>) => world.mock.find("POST", "/api/chat/conversations");
  const startPhone = () => screen.getByTestId("start-phone").textContent as string;
  /** Sends of the persona's script, counted the way the progress line must: the opening, each answer, the confirm replies or a plain yes, each interrupt with a say. */
  const scripted = (id: string) => {
    const p = PERSONAS.find((x) => x.id === id) as (typeof PERSONAS)[number];
    return 1 + Object.values(p.answers).reduce((n, list) => n + list.length, 0) + Math.max(p.confirm.length, 1) + p.interrupts.filter((i) => i.say).length;
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  describe("New take", () => {
    it("calls newTake with the chosen persona's country and locale and keeps that phone for Play", async () => {
      const world = makeWorld();
      await stageUp();
      expect(startPhone()).toMatch(/^\+52/);
      const region = await openConsole();
      choose(region, "qualified_es");
      setPace(region, "30", "30");
      await press(region, "New take");
      const fresh = startPhone();
      expect(fresh).toMatch(/^\+34\d{9}$/);
      expect(posts(world)).toHaveLength(0);
      await waitFor(() => expect(within(region).getByRole("button", { name: "Play" })).toBeEnabled());
      await press(region, "Play");
      await waitFor(() => expect(posts(world)).toHaveLength(1));
      expect(posts(world)[0].body).toMatchObject({ phone: fresh, country: "ES", locale: "es-ES", client: "demo" });
    });

    it("records a take when its conversation id appears, newest first, and never reuses a phone", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      expect(within(region).getByText("No takes yet. New take starts one.")).toBeInTheDocument();

      await press(region, "New take");
      await press(region, "Play");
      await waitFor(() => expect(rowsOf(region)).toHaveLength(1));
      await press(region, "Pause");
      await press(region, "New take");
      await press(region, "Play");
      await waitFor(() => expect(rowsOf(region)).toHaveLength(2));

      const [first, second] = posts(world).map((call) => (call.body as { phone: string }).phone);
      expect(first).not.toBe(second);
      const rows = rowsOf(region);
      expect(rows[0]).toHaveTextContent(second);
      expect(rows[1]).toHaveTextContent(first);
      expect(rows[0]).toHaveTextContent("confirm_correction_mx");
      const stored = JSON.parse(window.sessionStorage.getItem(TAKES_KEY) ?? "[]") as Take[];
      expect(stored.map((t) => t.phone)).toEqual([second, first]);
      expect(stored.map((t) => t.conversationId)).toEqual([...world.convs.keys()].reverse());
    });

    it("disables New take, Prepare take, Play, Step and Reopen while a run is active, and turns them on when it is paused", async () => {
      makeWorld();
      seedTakes([takeRow(1)]);
      await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      await press(region, "Play");
      await waitFor(() => expect(statusOf(region)).toBe("Running"));
      for (const name of ["New take", "Prepare take", "Play", "Step"]) expect(within(region).getByRole("button", { name })).toBeDisabled();
      for (const button of within(takesTable(region)).getAllByRole("button", { name: "Reopen" })) expect(button).toBeDisabled();
      await press(region, "Pause");
      for (const name of ["New take", "Prepare take", "Play", "Step"]) expect(within(region).getByRole("button", { name })).toBeEnabled();
    });
  });

  describe("Clear", () => {
    it("asks first, then empties the takes list and the phone and leaves a fresh New take, sending nothing", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      await press(region, "New take");
      await press(region, "Play");
      await waitFor(() => expect(rowsOf(region)).toHaveLength(1));
      await press(region, "Pause");
      // The phone holds the conversation, not the start card.
      await waitFor(() => expect(screen.queryByTestId("start-phone")).toBeNull());
      const used = (posts(world)[0].body as { phone: string }).phone;
      const calls = world.mock.calls.length;

      await press(region, "Clear");
      const dialog = await screen.findByRole("dialog", { name: "Clear the stage?" });
      expect(dialog).toHaveTextContent("Past takes stay on the dashboards.");
      fireEvent.click(within(dialog).getByRole("button", { name: "Clear the stage" }));
      await tick(0);

      expect(within(takesTable(region)).getByText("No takes yet. New take starts one.")).toBeInTheDocument();
      expect(window.sessionStorage.getItem(TAKES_KEY)).toBeNull();
      // The phone is empty again: the start card with a new number.
      const fresh = await waitFor(() => startPhone());
      expect(fresh).toMatch(/^\+52/);
      expect(fresh).not.toBe(used);
      // Client-side only: no write reached the server.
      expect(world.mock.calls.slice(calls).filter((call) => call.method !== "GET")).toHaveLength(0);
    });

    it("cancels without clearing anything", async () => {
      makeWorld();
      seedTakes([takeRow(1)]);
      await stageUp();
      const region = await openConsole();
      await press(region, "Clear");
      fireEvent.click(within(await screen.findByRole("dialog", { name: "Clear the stage?" })).getByRole("button", { name: "Cancel" }));
      await tick(0);
      expect(rowsOf(region)).toHaveLength(1);
    });

    it("is disabled while a take is running, like New take", async () => {
      makeWorld();
      await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      await press(region, "Play");
      await waitFor(() => expect(statusOf(region)).toBe("Running"));
      expect(within(region).getByRole("button", { name: "Clear" })).toBeDisabled();
      await press(region, "Pause");
      expect(within(region).getByRole("button", { name: "Clear" })).toBeEnabled();
    });
  });

  describe("the takes list", () => {
    it("shows the empty state", async () => {
      makeWorld();
      await stageUp();
      const region = await openConsole();
      expect(within(takesTable(region)).getByText("No takes yet. New take starts one.")).toBeInTheDocument();
    });

    it("shows a skeleton in the outcome column while the outcomes load", async () => {
      const world = makeWorld();
      world.flags.listHangs = true;
      seedTakes([takeRow(1, { conversationId: "wc_other", phone: "+525512340001" }), takeRow(2)]);
      world.seed("wc_other", "+525512340001", "Otro");
      await stageUp();
      const region = await openConsole();
      await waitFor(() => expect(within(takesTable(region)).getAllByRole("status", { name: "Loading" }).length).toBeGreaterThan(0));
      expect(rowsOf(region)).toHaveLength(2);
    });

    it("shows an error state with Retry when the lookup fails, and the outcomes after Retry", async () => {
      const world = makeWorld();
      world.seed(takeRow(2).conversationId, takeRow(2).phone, "Dos", [msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" })]);
      world.convs.get(takeRow(2).conversationId)!.outcome = "knockout";
      world.flags.listFails = true;
      seedTakes([takeRow(2)]);
      await stageUp();
      const region = await openConsole();
      const alert = await within(region).findByText("Could not look up the outcomes.");
      expect(alert).toBeInTheDocument();
      world.flags.listFails = false;
      fireEvent.click(within(region).getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(within(takesTable(region)).getByText("knockout")).toBeInTheDocument());
      expect(within(region).queryByText("Could not look up the outcomes.")).not.toBeInTheDocument();
    });

    it("looks the outcome up by the exact phone and never stores it", async () => {
      const world = makeWorld();
      const row = takeRow(2);
      world.seed(row.conversationId, row.phone, "Dos", [msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" })]);
      seedTakes([row]);
      await stageUp();
      const region = await openConsole();
      // The decoy (same digits plus one) is a Knockout; ours is open. The exact match decides.
      await waitFor(() => expect(rowsOf(region)[0]).toHaveTextContent("open"));
      expect(rowsOf(region)[0]).not.toHaveTextContent("knockout");
      const asked = world.mock.find("GET", "/api/applications").map((call) => call.query.get("q"));
      expect(asked).toContain(row.phone);
      expect(window.sessionStorage.getItem(TAKES_KEY)).not.toMatch(/open|knockout|outcome/);
    });
  });

  describe("Prepare take", () => {
    it("plays no_license_mx to its end at the fastest pace, shows n of m, and restores the presenter's pace and persona", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "45", "2");
      const total = scripted("no_license_mx");
      expect(total).toBeGreaterThan(4);

      await press(region, "Prepare take");
      await tick(50);
      await waitFor(() => expect(posts(world)).toHaveLength(1));
      expect(picker(region).value).toBe("no_license_mx");
      expect(within(region).getByLabelText("Characters per second")).toHaveValue(0);
      expect(within(region).getByLabelText("Pause before each message (seconds)")).toHaveValue(0);
      const conv = world.latest().id;
      expect(await within(region).findByText(`Preparing: 1 of ${total}`)).toBeInTheDocument();
      expect(statusOf(region)).toBe("Running");

      world.say(conv, "¿Cuál es tu zona?");
      await until(() => expect(within(region).getByText(`Preparing: 2 of ${total}`)).toBeInTheDocument());
      world.say(conv, "¿Tienes licencia?");
      await until(() => expect(within(region).getByText(`Preparing: 3 of ${total}`)).toBeInTheDocument());
      world.say(conv, "Necesitamos licencia vigente. Escríbenos cuando la tengas.");
      await until(() => expect(within(region).getByText(`Preparing: 4 of ${total}`)).toBeInTheDocument());
      world.latest().outcome = "knockout";
      world.say(conv, "Hecho: el equipo revisará la decisión.");

      // Not running; the presenter's persona and pace are back; the take is in the list and reads knockout.
      await until(() => expect(statusOf(region)).not.toBe("Running"));
      expect(within(region).queryByText(/Preparing:/)).not.toBeInTheDocument();
      expect(within(region).getByRole("button", { name: "Pause" })).toBeDisabled();
      expect(picker(region).value).toBe("confirm_correction_mx");
      expect(within(region).getByLabelText("Characters per second")).toHaveValue(45);
      expect(within(region).getByLabelText("Pause before each message (seconds)")).toHaveValue(2);
      await waitFor(() => expect(rowsOf(region)[0]).toHaveTextContent("knockout"));
      expect(rowsOf(region)[0]).toHaveTextContent("no_license_mx");
      expect(posts(world)).toHaveLength(1);
    });
  });

  describe("a stack with a live model", () => {
    const playTwice = async (region: HTMLElement) => {
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      await waitFor(() => expect(within(region).getByRole("button", { name: "Play" })).toBeEnabled());
      await press(region, "Play");
      await press(region, "Pause");
      await press(region, "New take");
    };

    it("shows the cost line and asks for a confirm before a second take, and a Cancel starts nothing", async () => {
      const world = makeWorld({ sim: false });
      await stageUp();
      const region = await openConsole();
      await waitFor(() => expect(within(region).getByText("About $0.01 per conversation")).toBeInTheDocument());
      await playTwice(region);
      expect(posts(world)).toHaveLength(1); // the first take needed no confirm
      await press(region, "Play");
      expect(within(region).getByRole("group", { name: "This stack uses a live model." })).toBeInTheDocument();
      expect(posts(world)).toHaveLength(1);
      await press(region, "Cancel");
      expect(within(region).queryByRole("group", { name: "This stack uses a live model." })).not.toBeInTheDocument();
      expect(posts(world)).toHaveLength(1);
      await press(region, "Play");
      await press(region, "Start another take");
      await waitFor(() => expect(posts(world)).toHaveLength(2));
    });

    it("asks the same confirm before Prepare take as a second take, and Cancel puts the pace and persona back", async () => {
      const world = makeWorld({ sim: false });
      await stageUp();
      const region = await openConsole();
      await waitFor(() => expect(within(region).getByText("About $0.01 per conversation")).toBeInTheDocument());
      await playTwice(region);
      await press(region, "Prepare take");
      expect(within(region).getByRole("group", { name: "This stack uses a live model." })).toBeInTheDocument();
      expect(posts(world)).toHaveLength(1);
      await press(region, "Cancel");
      expect(picker(region).value).toBe("confirm_correction_mx");
      expect(within(region).getByLabelText("Characters per second")).toHaveValue(30);
      expect(within(region).getByLabelText("Pause before each message (seconds)")).toHaveValue(30);
      expect(posts(world)).toHaveLength(1);
    });

    it("shows neither the cost line nor a confirm when the sim probe passes", async () => {
      const world = makeWorld({ sim: true });
      await stageUp();
      const region = await openConsole();
      await waitFor(() => expect(within(region).getByRole("button", { name: "Play" })).toBeDisabled());
      await playTwice(region);
      expect(within(region).queryByText("About $0.01 per conversation")).not.toBeInTheDocument();
      await press(region, "Play");
      expect(within(region).queryByRole("group", { name: "This stack uses a live model." })).not.toBeInTheDocument();
      await waitFor(() => expect(posts(world)).toHaveLength(2));
    });
  });

  describe("reopen and remount", () => {
    const seedTwo = (world: ReturnType<typeof makeWorld>) => {
      const a = takeRow(1);
      const b = takeRow(2);
      world.seed(a.conversationId, a.phone, "Candidato A", [
        msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola, soy A" }),
        msg({ id: 2, direction: "outbound", sender: "agent", text: "Mensaje de A" }),
      ]);
      world.seed(b.conversationId, b.phone, "Candidato B", [
        msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola, soy B" }),
        msg({ id: 2, direction: "outbound", sender: "agent", text: "Mensaje de B" }),
      ]);
      seedTakes([b, a]);
      return { a, b };
    };
    const messagesAsked = (world: ReturnType<typeof makeWorld>, id: string) => world.mock.calls.filter((c) => c.method === "GET" && c.path === `/api/chat/conversations/${id}/messages`);

    it("restores the list and resumes the last take when /stage mounts again", async () => {
      const world = makeWorld();
      const { a, b } = seedTwo(world);
      await stageUp();
      expect(await screen.findByText("Mensaje de B")).toBeInTheDocument();
      expect(messagesAsked(world, a.conversationId)).toHaveLength(0);
      expect(startPhone).toThrow(); // the start card is gone: a conversation is on the phone
      const region = await openConsole();
      expect(rowsOf(region).map((row) => row.textContent)).toEqual([expect.stringContaining(b.phone), expect.stringContaining(a.phone)]);
    });

    it("keeps the list across a real remount: a take made now is back, and the last one resumes", async () => {
      const world = makeWorld();
      const first = await stageUp();
      const region = await openConsole();
      choose(region, "confirm_correction_mx");
      setPace(region, "30", "30");
      await press(region, "Play");
      await waitFor(() => expect(rowsOf(region)).toHaveLength(1));
      const id = world.latest().id;
      const phone = world.latest().phone;
      first.unmount();

      await stageUp();
      await waitFor(() => expect(messagesAsked(world, id).length).toBeGreaterThan(1));
      expect(screen.queryByTestId("start-phone")).not.toBeInTheDocument();
      const again = await openConsole();
      expect(rowsOf(again)).toHaveLength(1);
      expect(rowsOf(again)[0]).toHaveTextContent(phone);
    });

    it("reopens a take: resumes its stored conversation, looks its Application up by the stored phone, and starts idle", async () => {
      const world = makeWorld();
      const { a } = seedTwo(world);
      await stageUp();
      expect(await screen.findByText("Mensaje de B")).toBeInTheDocument();
      const region = await openConsole();
      const before = posts(world).length;
      fireEvent.click(within(rowsOf(region)[1]).getByRole("button", { name: "Reopen" }));
      expect(await screen.findByText("Mensaje de A")).toBeInTheDocument();
      expect(messagesAsked(world, a.conversationId).length).toBeGreaterThan(0);
      // The record follows the stored phone: it is A's Application now, not B's.
      expect(await within(screen.getByRole("region", { name: "Record" })).findByRole("heading", { name: "Candidato A" })).toBeInTheDocument();
      expect(world.mock.find("GET", "/api/applications").some((call) => call.query.get("q") === a.phone)).toBe(true);
      // Idle: nothing running, nothing started.
      expect(statusOf(region)).not.toBe("Running");
      expect(within(region).getByRole("button", { name: "Pause" })).toBeDisabled();
      expect(posts(world)).toHaveLength(before);
      await tick(2000);
      expect(world.mock.calls.filter((c) => c.method === "POST" && c.path.endsWith("/messages"))).toHaveLength(0);
    });
  });

  describe("the clock section", () => {
    const stripOf = () => screen.getByRole("region", { name: "Simulated clock" });
    const consolePanelOpen = () => screen.queryByRole("region", CONSOLE) !== null;
    const clockCalls = (world: ReturnType<typeof makeWorld>, method: string) => world.mock.calls.filter((c) => c.method === method && c.path.startsWith("/api/sim/clock"));

    it("is absent on a stack with no sim probe, and the console sends no clock POST and asks no extra GET", async () => {
      const world = makeWorld({ sim: false });
      await stageUp();
      await waitFor(() => expect(clockCalls(world, "GET")).toHaveLength(1));
      const region = await openConsole();
      await tick(500);
      expect(within(region).queryByRole("group", { name: "Clock controls" })).not.toBeInTheDocument();
      expect(within(region).queryByRole("button", { name: /advance|reset clock/i })).not.toBeInTheDocument();
      expect(clockCalls(world, "POST")).toHaveLength(0);
      expect(clockCalls(world, "GET")).toHaveLength(1);
    });

    it("leaves the Simulated clock region without an Advance or Reset button, and asks the clock once with the console open", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      await within(region).findByRole("group", { name: "Clock controls" });
      expect(within(stripOf()).queryAllByRole("button")).toHaveLength(0);
      expect(clockCalls(world, "GET")).toHaveLength(1);
    });

    it("advances by a preset and by a seconds field, then refetches the clock so the strip shows the new time", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      const controls = await within(region).findByRole("group", { name: "Clock controls" });
      expect(stripOf()).toHaveTextContent("04:00");
      expect(clockCalls(world, "GET")).toHaveLength(1);

      fireEvent.click(within(controls).getByRole("button", { name: "Advance 1 hour" }));
      await waitFor(() => expect(world.mock.find("POST", "/api/sim/clock/advance")).toHaveLength(1));
      expect(world.mock.find("POST", "/api/sim/clock/advance")[0].body).toEqual({ seconds: 3600 });
      await waitFor(() => expect(clockCalls(world, "GET")).toHaveLength(2));
      await waitFor(() => expect(stripOf()).toHaveTextContent("05:00"));

      fireEvent.change(within(controls).getByLabelText("Advance by (seconds)"), { target: { value: "90" } });
      fireEvent.click(within(controls).getByRole("button", { name: "Advance" }));
      await waitFor(() => expect(world.mock.find("POST", "/api/sim/clock/advance")).toHaveLength(2));
      expect(world.mock.find("POST", "/api/sim/clock/advance")[1].body).toEqual({ seconds: 90 });
      await waitFor(() => expect(clockCalls(world, "GET")).toHaveLength(3));
      await waitFor(() => expect(stripOf()).toHaveTextContent("05:01"));
    });

    it("sends nothing for a seconds value the clock would refuse", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      const controls = await within(region).findByRole("group", { name: "Clock controls" });
      for (const bad of ["", "0", "-5", "1.5", "7776001"]) {
        fireEvent.change(within(controls).getByLabelText("Advance by (seconds)"), { target: { value: bad } });
        expect(within(controls).getByRole("button", { name: "Advance" })).toBeDisabled();
      }
      expect(world.mock.find("POST", "/api/sim/clock/advance")).toHaveLength(0);
    });

    it("gives focus back to Reset clock when the Dialog closes, on Cancel and on Escape", async () => {
      makeWorld();
      await stageUp();
      const region = await openConsole();
      const controls = await within(region).findByRole("group", { name: "Clock controls" });
      const reset = within(controls).getByRole("button", { name: "Reset clock" });
      for (const close of ["Cancel", "Escape"]) {
        reset.focus();
        fireEvent.click(reset);
        const dialog = await screen.findByRole("dialog");
        if (close === "Cancel") fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
        else fireEvent.keyDown(dialog, { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await tick(50);
        expect(reset).toHaveFocus();
        expect(consolePanelOpen()).toBe(true);
      }
    });

    it("asks in a Dialog before a reset, sends nothing on Cancel, and on confirm resets and refetches the clock", async () => {
      const world = makeWorld();
      await stageUp();
      const region = await openConsole();
      const controls = await within(region).findByRole("group", { name: "Clock controls" });
      fireEvent.click(within(controls).getByRole("button", { name: "Advance 1 day" }));
      await waitFor(() => expect(stripOf()).toHaveTextContent("10 Oct, 04:00"));

      fireEvent.click(within(controls).getByRole("button", { name: "Reset clock" }));
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("moves the clock back to real time");
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(world.mock.find("POST", "/api/sim/clock/reset")).toHaveLength(0);

      const gets = clockCalls(world, "GET").length;
      fireEvent.click(within(controls).getByRole("button", { name: "Reset clock" }));
      fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reset to real time" }));
      await waitFor(() => expect(world.mock.find("POST", "/api/sim/clock/reset")).toHaveLength(1));
      await waitFor(() => expect(clockCalls(world, "GET").length).toBe(gets + 1));
      await waitFor(() => expect(stripOf()).toHaveTextContent("09 Oct, 04:00"));
    });
  });

  describe("the recording window line", () => {
    it("reads the sim clock on a passing probe", async () => {
      makeWorld();
      await stageUp();
      const region = await openConsole();
      // The sim clock says 04:00 in Mexico City, whatever the browser's own time is.
      vi.setSystemTime(new Date("2026-10-12T20:00:00Z"));
      expect(await within(region).findByText("Outside the recording window (09:00 to 22:00)")).toBeInTheDocument();
      expect(within(region).getByTestId("console-time")).toHaveTextContent("04:00");
    });

    it("reads the browser's time when there is no sim clock", async () => {
      vi.setSystemTime(new Date("2026-10-12T16:30:00Z"));
      makeWorld({ sim: false });
      await stageUp();
      const region = await openConsole();
      expect(await within(region).findByText("Inside the recording window")).toBeInTheDocument();
      expect(within(region).getByTestId("console-time")).toHaveTextContent("10:30");
    });
  });
});
