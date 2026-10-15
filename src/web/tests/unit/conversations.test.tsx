// The Operator conversation list and its read-only view (operator console chat-history-web, CH4 and CH5).
// Written before pages/Conversations.tsx, pages/Conversation.tsx and lib/labels/conversations.ts exist.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { operations } from "../../src/api/schema";
import { formatDateTime } from "../../src/lib/format.ts";
import * as copy from "../../src/lib/labels/conversations.ts";
import { asOperator, mockApi, renderRoute } from "./helpers.tsx";
import { locations, operatorMe, recruiterMe } from "./fixtures.ts";

type ConversationList = operations["listConversations"]["responses"][200]["content"]["application/json"];
type ConversationItem = ConversationList["items"][number];
type ConversationDetail = operations["getConversation"]["responses"][200]["content"]["application/json"];

const STORAGE_KEY = "criba.testChat.conversation";
const ID_A = "wc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ID_B = "wc_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const ID_C = "wc_cccccccccccccccccccccccccccccccc";
const ID_D = "wc_dddddddddddddddddddddddddddddddd";
const ID_V = "wv_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";

function item(overrides: Partial<ConversationItem> = {}): ConversationItem {
  return {
    conversation_id: ID_A,
    channel: "webchat",
    client: "test_chat",
    persona_id: null,
    phone: "+525512345678",
    created_at: "2026-10-11T09:30:00Z",
    shared_phone: false,
    last_message_at: "2026-10-11T09:34:12Z",
    application: { id: "app_01JA2B3C4D5E6F7G8H9J0K", stage: "zone", outcome: null, status: "open" },
    ...overrides,
  };
}

function detail(overrides: Partial<ConversationDetail> = {}): ConversationDetail {
  return {
    ...item(),
    messages: [
      { id: 1, direction: "inbound", sender: "candidate", content_type: "text", body: "Hola", created_at: "2026-10-11T09:31:00Z" },
      {
        id: 2,
        direction: "outbound",
        sender: "agent",
        content_type: "text",
        body: "Hola, soy el asistente de Sazón.",
        created_at: "2026-10-11T09:31:02Z",
      },
    ],
    ...overrides,
  } as ConversationDetail;
}

const shellMocks = {
  "GET /api/locations": { items: locations },
  "GET /api/applications": { items: [], next_cursor: null, total: 0 },
  "GET /api/slots": { items: [] },
};

const previewOn = () => window.sessionStorage.setItem("criba.previewRecruiter", "1");

/** Every request whose path is under /api/conversations. */
const conversationCalls = (calls: { path: string }[]) => calls.filter((c) => c.path.startsWith("/api/conversations"));

/** The body rows of the list's table (the header row excluded). */
async function bodyRows() {
  const table = await screen.findByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CH4 the list", () => {
  it("names each row by persona, by phone without one, and Unknown persona for an id it does not know", async () => {
    mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations": {
        items: [
          item({ conversation_id: ID_A, client: "demo", persona_id: "confirm_correction_mx", phone: "+525500000001" }),
          item({ conversation_id: ID_B, client: "test_chat", persona_id: null, phone: "+525500000002" }),
          item({ conversation_id: ID_C, client: "demo", persona_id: "not_a_persona", phone: "+525500000003" }),
        ],
        next_cursor: null,
      },
    });
    renderRoute("/conversations");
    expect(await screen.findByRole("heading", { level: 1, name: "Test chat" })).toBeInTheDocument();
    const rows = await bodyRows();
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByRole("link", { name: "Diego Martínez" })).toBeInTheDocument();
    expect(within(rows[1]).getByRole("link", { name: "+525500000002" })).toBeInTheDocument();
    expect(within(rows[2]).getByRole("link", { name: "Unknown persona" })).toBeInTheDocument();
  });

  it("shows the client, Stage and Outcome, No Application yet, both times, the row links and New conversation", async () => {
    mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations": {
        items: [
          item({
            conversation_id: ID_A,
            client: "test_chat",
            application: { id: "app_1", stage: "zone", outcome: null, status: "open" },
          }),
          item({
            conversation_id: ID_B,
            client: "demo",
            persona_id: "confirm_correction_mx",
            application: { id: "app_2", stage: "driver_license", outcome: "knockout", status: "closed" },
          }),
          item({ conversation_id: ID_C, client: "test_chat", application: null, last_message_at: null, created_at: "2026-10-11T08:00:00Z" }),
        ],
        next_cursor: null,
      },
    });
    renderRoute("/conversations");
    const [a, b, c] = await bodyRows();

    expect(within(a).getByText("Test chat")).toBeInTheDocument();
    expect(within(a).getByText("Zone")).toBeInTheDocument();
    expect(within(a).getByText(formatDateTime("2026-10-11T09:30:00Z"))).toBeInTheDocument();
    expect(within(a).getByText(formatDateTime("2026-10-11T09:34:12Z"), { exact: false })).toBeInTheDocument();
    expect(within(a).getByRole("link", { name: "+525512345678" })).toHaveAttribute("href", `/conversations/${ID_A}`);

    expect(within(b).getByText("Demo stage")).toBeInTheDocument();
    expect(within(b).getByText("Driver's license")).toBeInTheDocument();
    expect(within(b).getByText("Knockout")).toBeInTheDocument();
    expect(within(b).getByRole("link", { name: "Diego Martínez" })).toHaveAttribute("href", `/conversations/${ID_B}`);

    expect(within(c).getByText("No Application yet")).toBeInTheDocument();
    expect(within(c).getByText(formatDateTime("2026-10-11T08:00:00Z"))).toBeInTheDocument();
    expect(within(c).getByRole("link")).toHaveAttribute("href", `/conversations/${ID_C}`);

    expect(screen.getByRole("link", { name: "New conversation" })).toHaveAttribute("href", "/test-chat");
  });

  it("shows Load more while there is a next page, asks for it by cursor and appends its rows", async () => {
    const api = mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations": (call) =>
        call.query.get("cursor") === "page-2"
          ? { items: [item({ conversation_id: ID_C, phone: "+525500000003" })], next_cursor: null }
          : {
              items: [item({ conversation_id: ID_A, phone: "+525500000001" }), item({ conversation_id: ID_B, phone: "+525500000002" })],
              next_cursor: "page-2",
            },
    });
    renderRoute("/conversations");
    expect(await bodyRows()).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(async () => expect(await bodyRows()).toHaveLength(3));
    const rows = await bodyRows();
    expect(within(rows[2]).getByRole("link", { name: "+525500000003" })).toBeInTheDocument();
    const lists = api.find("GET", "/api/conversations");
    expect(lists).toHaveLength(2);
    expect(lists[0].query.get("cursor")).toBeNull();
    expect(lists[1].query.get("cursor")).toBe("page-2");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument());
  });

  it("gives a Recruiter the not-found page at both paths and asks for no conversation", async () => {
    const api = mockApi({ "GET /api/auth/me": recruiterMe, ...shellMocks });
    const list = renderRoute("/conversations");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    list.unmount();
    renderRoute(`/conversations/${ID_A}`);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(conversationCalls(api.calls)).toEqual([]);
  });

  it("gives an Operator previewing the Recruiter view the not-found page at both paths and asks for no conversation", async () => {
    previewOn();
    const api = mockApi({ "GET /api/auth/me": operatorMe, ...shellMocks });
    const list = renderRoute("/conversations");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    list.unmount();
    renderRoute(`/conversations/${ID_A}`);
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(conversationCalls(api.calls)).toEqual([]);
  });
});

describe("CH5 the read-only view", () => {
  function renderView(conversation: ConversationDetail, extra: Record<string, object> = {}) {
    const api = mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations/:id": conversation,
      // /test-chat's long poll after a Resume: it never answers, so the page just waits.
      "GET /api/chat/conversations/:id/messages": () => new Promise<object>(() => {}),
      ...extra,
    });
    const view = renderRoute(`/conversations/${conversation.conversation_id}`);
    return { api, ...view };
  }

  it("shows the row's facts, the Application link and the Transcript, with no composer", async () => {
    const { api } = renderView(
      detail({
        conversation_id: ID_D,
        client: "demo",
        persona_id: "confirm_correction_mx",
        application: { id: "app_01JA2B3C4D5E6F7G8H9J0K", stage: "zone", outcome: null, status: "open" },
      }),
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Diego Martínez" })).toBeInTheDocument();
    expect(api.find("GET", `/api/conversations/${ID_D}`)).toHaveLength(1);
    expect(screen.getByText("Demo stage")).toBeInTheDocument();
    expect(screen.getByText("Zone")).toBeInTheDocument();
    expect(screen.getByText(formatDateTime("2026-10-11T09:30:00Z"), { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: copy.openApplication })).toHaveAttribute("href", "/applications/app_01JA2B3C4D5E6F7G8H9J0K");
    const log = screen.getByRole("log");
    expect(within(log).getByText("Hola")).toBeInTheDocument();
    expect(within(log).getByText("Hola, soy el asistente de Sazón.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send/i })).not.toBeInTheDocument();
    expect(document.querySelector("form")).toBeNull();
  });

  it("resumes an open conversation: writes its id under the Test chat key and opens /test-chat", async () => {
    const { router } = renderView(detail({ conversation_id: ID_D }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/test-chat"));
    expect(router.state.location.search).toBe("");
    expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe(ID_D);
  });

  it("offers Resume for a conversation with no message and no Application yet", async () => {
    renderView(detail({ conversation_id: ID_D, messages: [], last_message_at: null, application: null }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("No Application yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: copy.openApplication })).not.toBeInTheDocument();
  });

  it("has no Resume and says why when the Application is closed", async () => {
    renderView(
      detail({
        conversation_id: ID_D,
        application: { id: "app_closed", stage: "driver_license", outcome: "knockout", status: "closed" },
      }),
    );
    expect(await screen.findByText("This conversation's Application is closed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("stays usable and says so politely when session storage refuses the id", async () => {
    const original = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key: string, value: string) {
      if (this === window.sessionStorage && key === STORAGE_KEY) throw new DOMException("storage blocked", "SecurityError");
      return original.call(this, key, value);
    });
    const { router } = renderView(detail({ conversation_id: ID_D }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
    const status = await screen.findByText(copy.resumeFailed);
    expect(status.closest('[role="status"], [aria-live="polite"]')).not.toBeNull();
    expect(router.state.location.pathname).toBe(`/conversations/${ID_D}`);
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
    expect(screen.getByRole("log")).toBeInTheDocument();
  });

  it("notes shared messages only when the phone has other conversations", async () => {
    const note = "Messages from every web-chat conversation with this phone.";
    const shared = renderView(detail({ conversation_id: ID_D, shared_phone: true }));
    expect(await screen.findByText(note)).toBeInTheDocument();
    shared.unmount();
    renderView(detail({ conversation_id: ID_C, shared_phone: false }));
    expect(await screen.findByRole("log")).toBeInTheDocument();
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });
});

describe("voice conversations (ADR 0067, owner decision 2026-10-12)", () => {
  it("tags the voice row and the voice view Spoken, and no webchat one", async () => {
    mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations": {
        items: [
          item({ conversation_id: ID_V, channel: "voice", phone: "+525500000009" }),
          item({ conversation_id: ID_A, channel: "webchat", phone: "+525500000001" }),
        ],
        next_cursor: null,
      },
      "GET /api/conversations/:id": (call) =>
        call.path.endsWith(ID_V)
          ? detail({ conversation_id: ID_V, channel: "voice", phone: "+525500000009" })
          : detail({ conversation_id: ID_A, channel: "webchat", phone: "+525500000001" }),
    });
    const list = renderRoute("/conversations");
    const [voice, typed] = await bodyRows();
    expect(within(voice).getByText("Spoken")).toBeInTheDocument();
    expect(within(voice).getByText("Test chat")).toBeInTheDocument();
    expect(within(typed).queryByText("Spoken")).not.toBeInTheDocument();
    list.unmount();

    const voiceView = renderRoute(`/conversations/${ID_V}`);
    const header = (await screen.findByRole("heading", { level: 1, name: "+525500000009" })).closest(".page-head") as HTMLElement;
    expect(within(header).getByText("Spoken")).toBeInTheDocument();
    voiceView.unmount();

    renderRoute(`/conversations/${ID_A}`);
    await screen.findByRole("heading", { level: 1, name: "+525500000001" });
    expect(screen.queryByText("Spoken")).not.toBeInTheDocument();
  });

  it("offers no Resume for a voice conversation whose Application is open, and says why", async () => {
    mockApi({
      ...asOperator,
      ...shellMocks,
      "GET /api/conversations/:id": detail({
        conversation_id: ID_V,
        channel: "voice",
        application: { id: "app_voice", stage: "zone", outcome: null, status: "open" },
      }),
    });
    renderRoute(`/conversations/${ID_V}`);
    expect(await screen.findByText("Spoken conversations cannot be resumed here.")).toHaveTextContent(
      /^Spoken conversations cannot be resumed here\.$/,
    );
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    expect(screen.queryByText("This conversation's Application is closed.")).not.toBeInTheDocument();
  });
});
