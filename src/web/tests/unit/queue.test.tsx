import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { queueItem, recruiterMe, summary } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

let streams: { url: string; fire: () => void; open: () => void; closed: boolean }[] = [];

beforeEach(() => {
  streams = [];
  vi.stubGlobal(
    "EventSource",
    class {
      entry: (typeof streams)[number];
      listeners: Record<string, ((e: Event) => void)[]> = {};
      constructor(url: string) {
        const emit = (name: string, event: Event) => (this.listeners[name] ?? []).forEach((fn) => fn(event));
        this.entry = {
          url,
          closed: false,
          fire: () => emit("queue", new MessageEvent("queue", { data: JSON.stringify({ type: "queue", at: "2026-10-09T11:00:00Z", data: {} }) })),
          open: () => emit("open", new Event("open")),
        };
        streams.push(this.entry);
      }
      addEventListener(name: string, fn: (e: Event) => void) {
        (this.listeners[name] ??= []).push(fn);
      }
      removeEventListener() {}
      close() {
        this.entry.closed = true;
      }
    },
  );
});

const items = [
  queueItem({ id: "qi_approval_1", kind: "approval" }),
  queueItem({ id: "qi_review_1", kind: "review", application: summary({ id: "app_k", status: "closed", outcome: "knockout", knockout_criterion: "driver_license", flags: ["review_requested"] }) }),
  queueItem({ id: "qi_esc_7", kind: "escalation", reason: "unanswered_question", note: "Asks whether the motorbike is provided.", application: summary({ id: "app_e", stage: "experience", flags: ["escalation"] }) }),
];

function setup() {
  return mockApi({
    "GET /api/auth/me": recruiterMe,
    "GET /api/queue": { items, next_cursor: null },
    "POST /api/queue/approvals/:id": { ...items[0], status: "resolved" },
    "POST /api/queue/reviews/:id": { ...items[1], status: "resolved" },
    "POST /api/queue/escalations/:id": { ...items[2], status: "resolved" },
    "GET /api/applications": {
      items: [summary({ id: "app_r", status: "closed", outcome: "rejection", rejection_reason: "no_show", rejection_note: "Did not come to the Interview.", stage: "interview_booked", closed_at: "2026-10-15T09:00:00Z" })],
      next_cursor: null,
      total: 1,
    },
    "GET /api/applications/:id": error(404, "not_found", "n/a"),
  });
}

describe("Review queue", () => {
  it("groups items into Approvals, Reviews, Escalations and Rejections with counts", async () => {
    setup();
    renderRoute("/queue");
    expect(await screen.findByRole("tab", { name: "Approvals (1)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Reviews (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Escalations (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Rejections" })).toBeInTheDocument();
    expect(screen.getByText("Qualified and waiting for Approval.")).toBeInTheDocument();
  });

  it("approves an Approval", async () => {
    const mock = setup();
    renderRoute("/queue");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")[0].body).toEqual({ decision: "approved" });
  });

  it("rejects an Approval with a reason from the list", async () => {
    const mock = setup();
    renderRoute("/queue");
    fireEvent.click(await screen.findByRole("button", { name: "Reject..." }));
    const form = screen.getByRole("form", { name: "Rejection" });
    expect(within(form).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a reason",
      "No-show",
      "Failed call",
      "Role filled",
      "Other (note required)",
    ]);
    fireEvent.change(within(form).getByLabelText("Reason"), { target: { value: "role_filled" } });
    fireEvent.click(within(form).getByRole("button", { name: "Confirm Rejection" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")[0].body).toEqual({ decision: "rejected", reason: "role_filled", note: null });
  });

  it("resolves a Review as Upheld or Overturned with a note", async () => {
    const mock = setup();
    renderRoute("/queue?tab=review");
    expect(await screen.findByText(/asked for a Review of the Knockout \(driver's license\)/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "Has a license." } });
    fireEvent.click(screen.getByRole("button", { name: "Overturned" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/reviews/qi_review_1")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/reviews/qi_review_1")[0].body).toEqual({ decision: "overturned", note: "Has a license." });
  });

  it("dismisses an Escalation", async () => {
    const mock = setup();
    renderRoute("/queue?tab=escalation");
    expect(await screen.findByText("Question the Screening Agent could not answer.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/escalations/qi_esc_7")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/escalations/qi_esc_7")[0].body).toEqual({ decision: "dismissed", note: null });
  });

  it("starts a Takeover from an Escalation and opens the Application", async () => {
    const mock = setup();
    const { router } = renderRoute("/queue?tab=escalation");
    fireEvent.click(await screen.findByRole("button", { name: "Start Takeover" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/applications/app_e"));
    expect(mock.find("POST", "/api/queue/escalations/qi_esc_7")[0].body).toEqual({ decision: "takeover", note: null });
  });

  it("surfaces a conflict when the item was already resolved", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": { items, next_cursor: null },
      "POST /api/queue/approvals/:id": error(409, "already_resolved", "Pedro Ruiz already resolved this Approval."),
    });
    renderRoute("/queue");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pedro Ruiz already resolved this Approval.");
  });

  it("lists Rejections with their reasons and notes", async () => {
    setup();
    renderRoute("/queue?tab=rejection");
    const table = await screen.findByRole("table");
    expect(await within(table).findByText("No-show")).toBeInTheDocument();
    expect(within(table).getByText("Did not come to the Interview.")).toBeInTheDocument();
    expect(within(table).getByText("Interview booked")).toBeInTheDocument();
  });

  it("refreshes from the queue stream", async () => {
    const mock = setup();
    renderRoute("/queue");
    await screen.findByRole("tab", { name: "Approvals (1)" });
    const stream = streams.find((s) => s.url === "/api/queue/stream")!;
    expect(stream).toBeDefined();
    const before = mock.find("GET", "/api/queue").length;
    act(() => stream.fire());
    await waitFor(() => expect(mock.find("GET", "/api/queue").length).toBeGreaterThan(before));
  });

  describe("Rejections tab stays current", () => {
    const rejected = (id: string, reason: "no_show" | "role_filled", note: string | null) =>
      summary({ id, status: "closed", outcome: "rejection", rejection_reason: reason, rejection_note: note, stage: "interview_booked", closed_at: "2026-10-15T09:00:00Z" });

    function setupRejections() {
      const rows = [rejected("app_r", "no_show", "Did not come to the Interview.")];
      const mock = mockApi({
        "GET /api/auth/me": recruiterMe,
        "GET /api/queue": { items, next_cursor: null },
        "GET /api/applications": () => ({ items: rows, next_cursor: null, total: rows.length }),
      });
      return { mock, rows };
    }

    it("shows a Rejection made after the page loaded when a queue frame arrives", async () => {
      const { mock, rows } = setupRejections();
      renderRoute("/queue?tab=rejection");
      const table = await screen.findByRole("table");
      expect(await within(table).findByText("No-show")).toBeInTheDocument();
      expect(within(table).queryByText("Role filled")).not.toBeInTheDocument();

      rows.unshift(rejected("app_new", "role_filled", "Rejected by another Recruiter."));
      const before = mock.find("GET", "/api/applications").length;
      act(() => streams.find((s) => s.url === "/api/queue/stream")!.fire());

      expect(await within(table).findByText("Role filled")).toBeInTheDocument();
      expect(within(table).getByText("Rejected by another Recruiter.")).toBeInTheDocument();
      expect(mock.find("GET", "/api/applications").length).toBeGreaterThan(before);
    });

    it("refetches the Rejections on every stream (re)connect", async () => {
      const { mock, rows } = setupRejections();
      renderRoute("/queue?tab=rejection");
      const table = await screen.findByRole("table");
      expect(await within(table).findByText("No-show")).toBeInTheDocument();
      const stream = streams.find((s) => s.url === "/api/queue/stream")!;

      // A Rejection made on an Application page produces no queue frame; the reconnect catches it.
      rows.unshift(rejected("app_new", "role_filled", null));
      const before = mock.find("GET", "/api/applications").length;
      act(() => stream.open());

      expect(await within(table).findByText("Role filled")).toBeInTheDocument();
      expect(mock.find("GET", "/api/applications").length).toBe(before + 1);
    });
  });
});

// Phase 4 (recruiter-pages): three states on every tab, actions tied to their item, and a switch that
// never shows another list's items. Appended; every test above stays as it was.

describe("Review queue layout and actions", () => {
  const forever = () => new Promise<object>(() => undefined);

  it("keeps the tablist, tab and tabpanel roles, with the panel named by its tab", async () => {
    setup();
    renderRoute("/queue");
    await screen.findByRole("tab", { name: "Approvals (1)" });
    const list = screen.getByRole("tablist", { name: "Queue kinds" });
    expect(within(list).getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByRole("tab", { name: "Reviews (1)" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tabpanel", { name: "Approvals" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Rejections" }));
    expect(await screen.findByRole("tabpanel", { name: "Rejections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Rejections" })).toHaveAttribute("aria-selected", "true");
  });

  it("puts the Show select in the header and keeps the status parameter", async () => {
    setup();
    const { router } = renderRoute("/queue");
    const show = await screen.findByLabelText("Show");
    expect(show.closest(".page-head")).not.toBeNull();
    fireEvent.change(show, { target: { value: "resolved" } });
    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get("status")).toBe("resolved"));
  });

  it("keeps the item as a named article that holds its Tag, context and actions", async () => {
    setup();
    renderRoute("/queue?tab=escalation");
    const article = await screen.findByRole("article", { name: "Escalation for Luis Hernández" });
    expect(within(article).getByRole("link", { name: "Luis Hernández" })).toHaveAttribute("href", "/applications/app_e");
    expect(within(article).getByText(/waiting since/)).toBeInTheDocument();
    expect(within(within(article).getByRole("heading", { level: 3 })).getByText("Escalation")).toBeInTheDocument();
    expect(within(article).getByText("Question the Screening Agent could not answer.")).toBeInTheDocument();
  });

  it("tones the actions: Approve, Overturned and Start Takeover are primary, Reject... is danger", async () => {
    setup();
    const first = renderRoute("/queue");
    expect(await screen.findByRole("button", { name: "Approve" })).toHaveClass("ui-btn--primary");
    expect(screen.getByRole("button", { name: "Reject..." })).toHaveClass("ui-btn--danger");
    first.unmount();
    const second = renderRoute("/queue?tab=review");
    expect(await screen.findByRole("button", { name: "Overturned" })).toHaveClass("ui-btn--primary");
    expect(screen.getByRole("button", { name: "Upheld" })).toHaveClass("ui-btn--secondary");
    second.unmount();
    renderRoute("/queue?tab=escalation");
    expect(await screen.findByRole("button", { name: "Start Takeover" })).toHaveClass("ui-btn--primary");
    expect(screen.getByRole("button", { name: "Dismiss" })).toHaveClass("ui-btn--secondary");
  });

  it("points each repeated action at the item it acts on, keeping the names", async () => {
    setup();
    for (const [tab, names] of [
      ["approval", ["Approve", "Reject..."]],
      ["review", ["Upheld", "Overturned"]],
      ["escalation", ["Start Takeover", "Dismiss"]],
    ] as const) {
      const view = renderRoute(`/queue?tab=${tab}`);
      const article = (await screen.findAllByRole("article"))[0];
      for (const name of names) {
        const button = within(article).getByRole("button", { name });
        const target = document.getElementById(button.getAttribute("aria-describedby") ?? "");
        expect(target, `${tab} ${name}`).not.toBeNull();
        expect(article).toContainElement(target);
        expect(target).toHaveTextContent("Luis Hernández");
      }
      view.unmount();
    }
  });

  it("disables the actions of an item while its request is pending", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": { items, next_cursor: null },
      "POST /api/queue/approvals/:id": forever,
      "POST /api/queue/reviews/:id": forever,
      "POST /api/queue/escalations/:id": forever,
    });
    const approvals = renderRoute("/queue");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled());
    approvals.unmount();
    const reviews = renderRoute("/queue?tab=review");
    fireEvent.click(await screen.findByRole("button", { name: "Overturned" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Overturned" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Upheld" })).toBeDisabled();
    reviews.unmount();
    renderRoute("/queue?tab=escalation");
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Start Takeover" })).toBeDisabled();
  });

  it("shows a failed action as an alert inside the item", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": { items, next_cursor: null },
      "POST /api/queue/approvals/:id": error(409, "already_resolved", "Pedro Ruiz already resolved this Approval."),
    });
    renderRoute("/queue");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const article = screen.getByRole("article", { name: "Approval for Luis Hernández" });
    expect(await within(article).findByRole("alert")).toHaveTextContent("Pedro Ruiz already resolved this Approval.");
  });

  it("keeps QueueCard private to the page", async () => {
    const module = await import("../../src/pages/ReviewQueue.tsx");
    expect(Object.keys(module)).toEqual(["ReviewQueue"]);
  });
});

describe("Review queue states", () => {
  const forever = () => new Promise<object>(() => undefined);
  const skeletons = () => document.querySelectorAll(".ui-skeleton").length;

  it("shows a skeleton and no Loading text on first load, with no counts yet", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, "GET /api/queue": forever });
    renderRoute("/queue");
    await screen.findByRole("heading", { level: 1, name: "Review queue" });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.queryByText(/Nothing|No Approvals/)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("status")).toEqual([]);
  });

  it.each([
    ["approval", /No Approvals waiting/, /Qualified Applications wait here for your Approval/],
    ["review", /No Reviews waiting/, /asks for a Review of a Knockout lands here/],
    ["escalation", /No Escalations waiting/, /Questions the Screening Agent could not answer land here/],
  ])("says what lands in an empty %s tab", async (tab, title, hint) => {
    mockApi({ "GET /api/auth/me": recruiterMe, "GET /api/queue": { items: [], next_cursor: null } });
    renderRoute(`/queue?tab=${tab}`);
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText(hint)).toBeInTheDocument();
    expect(skeletons()).toBe(0);
  });

  it("says nothing is resolved yet on an empty resolved list", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, "GET /api/queue": { items: [], next_cursor: null } });
    renderRoute("/queue?status=resolved");
    expect(await screen.findByText(/No resolved Approvals yet/)).toBeInTheDocument();
  });

  it("shows an error state whose Retry asks for the queue once more", async () => {
    let failing = true;
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": () => (failing ? error(500, "internal", "boom") : { items, next_cursor: null }),
    });
    renderRoute("/queue");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the queue. boom");
    expect(screen.getByRole("tablist", { name: "Queue kinds" })).toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    const before = mock.find("GET", "/api/queue").length;
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("tab", { name: "Approvals (1)" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/queue")).toHaveLength(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the items and shows no skeleton when a stream frame refreshes the queue", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": () => (++calls === 1 ? { items, next_cursor: null } : forever()),
    });
    renderRoute("/queue");
    await screen.findByRole("tab", { name: "Approvals (1)" });
    act(() => streams.find((s) => s.url === "/api/queue/stream")!.fire());
    await waitFor(() => expect(mock.find("GET", "/api/queue")).toHaveLength(2));
    expect(screen.getByRole("article", { name: "Approval for Luis Hernández" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals (1)" })).toBeInTheDocument();
    expect(skeletons()).toBe(0);
  });

  it("shows the skeleton, not the open items, when Show switches to resolved", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/queue": (call) => (call.query.get("status") === "resolved" ? forever() : { items, next_cursor: null }),
    });
    renderRoute("/queue");
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "resolved" } });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryAllByRole("article")).toEqual([]);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject..." })).not.toBeInTheDocument();
  });

  describe("Rejections tab", () => {
    it("shows a skeleton and no Loading text while the list loads", async () => {
      mockApi({ "GET /api/auth/me": recruiterMe, "GET /api/queue": { items, next_cursor: null }, "GET /api/applications": forever });
      renderRoute("/queue?tab=rejection");
      await screen.findByRole("tabpanel", { name: "Rejections" });
      await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
      expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
      expect(screen.queryByText("No Rejections yet.")).not.toBeInTheDocument();
    });

    it("says what lands there when there are none, once", async () => {
      mockApi({ "GET /api/auth/me": recruiterMe, "GET /api/queue": { items, next_cursor: null }, "GET /api/applications": { items: [], next_cursor: null, total: 0 } });
      renderRoute("/queue?tab=rejection");
      const table = await screen.findByRole("table");
      expect(await within(table).findAllByText("No Rejections yet.")).toHaveLength(1);
      expect(screen.getAllByText("No Rejections yet.")).toHaveLength(1);
    });

    it("shows an error state whose Retry asks for the Rejections once more", async () => {
      let failing = true;
      const mock = mockApi({
        "GET /api/auth/me": recruiterMe,
        "GET /api/queue": { items, next_cursor: null },
        "GET /api/applications": () =>
          failing ? error(500, "internal", "boom") : { items: [summary({ id: "app_r", status: "closed", outcome: "rejection", rejection_reason: "no_show", stage: "zone", closed_at: "2026-10-15T09:00:00Z" })], next_cursor: null, total: 1 },
      });
      renderRoute("/queue?tab=rejection");
      expect(await screen.findByRole("alert")).toHaveTextContent("Could not load Rejections. boom");
      const before = mock.find("GET", "/api/applications").length;
      failing = false;
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByText("No-show")).toBeInTheDocument();
      expect(mock.find("GET", "/api/applications")).toHaveLength(before + 1);
    });

    it("keeps the rows and shows no skeleton when the list refetches", async () => {
      let calls = 0;
      const mock = mockApi({
        "GET /api/auth/me": recruiterMe,
        "GET /api/queue": { items, next_cursor: null },
        "GET /api/applications": () =>
          ++calls === 1
            ? { items: [summary({ id: "app_r", status: "closed", outcome: "rejection", rejection_reason: "no_show", stage: "zone", closed_at: "2026-10-15T09:00:00Z" })], next_cursor: null, total: 1 }
            : forever(),
      });
      renderRoute("/queue?tab=rejection");
      expect(await screen.findByText("No-show")).toBeInTheDocument();
      act(() => streams.find((s) => s.url === "/api/queue/stream")!.fire());
      await waitFor(() => expect(mock.find("GET", "/api/applications")).toHaveLength(2));
      expect(screen.getByText("No-show")).toBeInTheDocument();
      expect(skeletons()).toBe(0);
    });

    it("names its columns", async () => {
      setup();
      renderRoute("/queue?tab=rejection");
      const table = await screen.findByRole("table");
      expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
        "Candidate",
        "Stage when rejected",
        "Reason",
        "Rejected",
        "Total time",
      ]);
    });
  });
});
