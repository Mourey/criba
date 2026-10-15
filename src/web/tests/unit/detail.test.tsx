import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Message } from "../../src/api/types.ts";
import { claimByAna, detail, operatorMe, recruiterMe } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

const ID = "app_01JA2B3C4D5E6F7G8H9J0K";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((e: Event) => void)[]>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (e: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  removeEventListener(name: string, fn: (e: Event) => void) {
    this.listeners.set(name, (this.listeners.get(name) ?? []).filter((f) => f !== fn));
  }
  close() {
    this.closed = true;
  }
  emit(name: string, payload: unknown) {
    for (const fn of this.listeners.get(name) ?? []) fn(new MessageEvent(name, { data: JSON.stringify(payload) }));
  }
  open() {
    for (const fn of this.listeners.get("open") ?? []) fn(new Event("open"));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("Application detail", () => {
  it("shows the transcript, answers with validation status, timeline, summary and other Applications", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);

    const transcript = await screen.findByRole("log", { name: "Transcript" });
    expect(within(transcript).getByText("Hola, quiero trabajar de repartidor")).toBeInTheDocument();
    expect(within(transcript).getAllByText("Screening Agent").length).toBeGreaterThan(0);
    expect(within(transcript).getByText("Tiempo completo")).toBeInTheDocument();

    const answers = screen.getByRole("heading", { name: "Answers" }).closest("section")!;
    expect(within(answers).getByText("Centro, Puebla")).toBeInTheDocument();
    expect(within(answers).getByText("sí, tengo licencia")).toBeInTheDocument();
    expect(within(answers).getByText("more than 60 days out")).toBeInTheDocument();
    expect(within(answers).getByText("Flagged")).toBeInTheDocument();

    const timeline = screen.getByRole("heading", { name: "Timeline" }).closest("section")!;
    expect(within(timeline).getByText("Application started")).toBeInTheDocument();
    expect(within(timeline).getByText("Screening finished")).toBeInTheDocument();

    expect(screen.getByText("Luis lives in central Puebla and holds a valid licence.")).toBeInTheDocument();
    expect(screen.getByText("No files. Phase 2 collects no documents.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2026-06-02" })).toHaveAttribute("href", "/applications/app_01J9ZZ");
  });

  it("links the Application JSON export", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    const link = await screen.findByRole("link", { name: "Export JSON" });
    expect(link).toHaveAttribute("href", `/api/applications/${ID}/export`);
    expect(link).toHaveAttribute("download");
  });

  it("links the booked Interview's invite for the Recruiter, and only when there is one", async () => {
    const interview = {
      starts_at: "2026-10-14T16:00:00Z",
      ends_at: "2026-10-14T16:30:00Z",
      local_label: "mié 14 · 10:00",
      location_name: "Sazón Puebla Centro",
      address: "Av. Reforma 123, Centro, Puebla",
    };
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: { ...detail(), interview } });
    const booked = renderRoute(`/applications/${ID}`);
    const link = await screen.findByRole("link", { name: "Download invite (.ics)" });
    expect(link).toHaveAttribute("href", `/api/applications/${ID}/booking/invite.ics`);
    expect(link).toHaveAttribute("download");
    expect(link.closest("section")).toHaveTextContent("mié 14 · 10:00 at Sazón Puebla Centro");
    booked.unmount();

    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    expect(screen.queryByRole("link", { name: "Download invite (.ics)" })).not.toBeInTheDocument();
  });

  it("shows cost to Operators but never to Recruiters or while previewing the Recruiter view", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, [`GET /api/applications/:id`]: detail() });
    const operator = renderRoute(`/applications/${ID}`);
    expect(await screen.findByRole("heading", { name: "Cost (Operators only)" })).toBeInTheDocument();
    expect(screen.getByText("$0.0031")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Cost (Operators only)" })).not.toBeInTheDocument());
    operator.unmount();

    // A real Recruiter response has no cost object at all.
    const { cost: _cost, ...withoutCost } = detail();
    void _cost;
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: withoutCost });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    expect(screen.queryByText(/Cost \(Operators only\)/)).not.toBeInTheDocument();
  });

  it("claims, and shows the server's message when another Recruiter holds the Claim", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail(),
      [`POST /api/applications/:id/claim`]: error(409, "already_claimed", "Pedro Ruiz already holds the Claim."),
    });
    renderRoute(`/applications/${ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Claim" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pedro Ruiz already holds the Claim.");
    expect(mock.find("POST", `/api/applications/${ID}/claim`)).toHaveLength(1);
  });

  it("releases my own Claim and offers Take over only for someone else's idle Claim", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ claim: claimByAna }),
      [`DELETE /api/applications/:id/claim`]: [204, undefined],
    });
    renderRoute(`/applications/${ID}`);
    expect(screen.queryByRole("button", { name: "Take over Claim" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Release Claim" }));
    await waitFor(() => expect(mock.find("DELETE", `/api/applications/${ID}/claim`)).toHaveLength(1));
  });

  it("takes over a Claimed, idle Claim held by someone else", async () => {
    const idle = { ...claimByAna, user_id: "usr_other", user_name: "Pedro Ruiz", idle: true };
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ claim: idle, flags: ["claimed_idle"] }),
      [`POST /api/applications/:id/claim/takeover`]: claimByAna,
    });
    renderRoute(`/applications/${ID}`);
    expect(screen.queryByRole("button", { name: "Claim" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Take over Claim" }));
    await waitFor(() => expect(mock.find("POST", `/api/applications/${ID}/claim/takeover`)).toHaveLength(1));
  });

  it("rejects with a reason from the list, and needs a note for Other", async () => {
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail(),
      [`POST /api/applications/:id/rejection`]: detail({ status: "closed", outcome: "rejection" }),
    });
    renderRoute(`/applications/${ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Reject..." }));
    const form = screen.getByRole("form", { name: "Rejection" });
    const confirm = within(form).getByRole("button", { name: "Confirm Rejection" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("Reason"), { target: { value: "other" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(form).getByLabelText(/Note/), { target: { value: "Lives too far away." } });
    fireEvent.click(confirm);
    await waitFor(() => expect(mock.find("POST", `/api/applications/${ID}/rejection`)).toHaveLength(1));
    expect(mock.find("POST", `/api/applications/${ID}/rejection`)[0].body).toEqual({ reason: "other", note: "Lives too far away." });
  });

  it("creates a booking link for an Approved Application", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ stage: "approved" }),
      [`POST /api/applications/:id/booking-link`]: [201, { url: "https://empleo.example/book/abc", token: "abc", expires_at: "2026-10-16T10:00:00Z" }],
    });
    renderRoute(`/applications/${ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Create booking link" }));
    expect(await screen.findByRole("link", { name: "https://empleo.example/book/abc" })).toBeInTheDocument();
  });

  it("says so when the Application is outside the Recruiter's Locations", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: error(404, "not_found", "Application not found.") });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByRole("heading", { name: "Application not found" })).toBeInTheDocument();
  });
});

describe("Takeover panel", () => {
  const inbound: Message = {
    id: 43,
    direction: "inbound",
    sender: "candidate",
    sender_name: null,
    content_type: "text",
    body: "¡Perfecto, gracias!",
    interactive: null,
    created_at: "2026-10-09T11:04:00Z",
  };

  it("starts a Takeover, relays messages both ways live, and hands back with a summary", async () => {
    let control: "agent" | "human" = "agent";
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: () => detail({ control, control_user_id: control === "human" ? "usr_rec_puebla" : null, control_user_name: control === "human" ? "Ana Torres" : null }),
      [`POST /api/applications/:id/takeover`]: () => {
        control = "human";
        return { control: "human", user_id: "usr_rec_puebla", user_name: "Ana Torres", since: "2026-10-09T11:02:00Z" };
      },
      [`POST /api/applications/:id/takeover/messages`]: [
        201,
        { id: 42, direction: "outbound", sender: "human", sender_name: "Ana Torres", content_type: "text", body: "Hola Luis, soy Ana.", interactive: null, created_at: "2026-10-09T11:03:00Z" },
      ],
      [`POST /api/applications/:id/takeover/handback`]: () => {
        control = "agent";
        return { control: "agent", user_id: null, user_name: null, since: "2026-10-09T11:10:00Z" };
      },
    });
    renderRoute(`/applications/${ID}`);

    fireEvent.click(await screen.findByRole("button", { name: "Start Takeover" }));
    const box = await screen.findByLabelText("Message to the Candidate");
    expect(screen.getByText(/The Screening Agent is paused/)).toBeInTheDocument();

    // Outbound: the message is appended at once and sent to the API.
    fireEvent.change(box, { target: { value: "Hola Luis, soy Ana." } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const transcript = screen.getByRole("log", { name: "Transcript" });
    expect(await within(transcript).findByText("Hola Luis, soy Ana.")).toBeInTheDocument();
    expect(within(transcript).getByText("Ana Torres")).toBeInTheDocument();
    expect(mock.find("POST", `/api/applications/${ID}/takeover/messages`)[0].body).toEqual({ text: "Hola Luis, soy Ana." });

    // Inbound: arrives over the Application's event stream, and a repeated frame is not doubled.
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    expect(stream).toBeDefined();
    act(() => stream.emit("message", { type: "message", at: inbound.created_at, data: inbound }));
    act(() => stream.emit("message", { type: "message", at: inbound.created_at, data: inbound }));
    expect(await within(transcript).findAllByText("¡Perfecto, gracias!")).toHaveLength(1);

    // Hand back with a summary.
    fireEvent.change(screen.getByLabelText(/Summary for the Screening Agent/), { target: { value: "Confirmed the motorbike is provided." } });
    fireEvent.click(screen.getByRole("button", { name: "Hand back to the Screening Agent" }));
    expect(await screen.findByRole("button", { name: "Start Takeover" })).toBeInTheDocument();
    expect(mock.find("POST", `/api/applications/${ID}/takeover/handback`)[0].body).toEqual({ summary: "Confirmed the motorbike is provided." });
  });

  it("shows who holds the chat when it is someone else, with no way to reply", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ control: "human", control_user_id: "usr_other", control_user_name: "Pedro Ruiz", flags: ["takeover"] }),
    });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByText(/Pedro Ruiz holds this chat/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start Takeover" })).not.toBeInTheDocument();
  });

  it("does not treat a Takeover by another user with the same name as mine", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ control: "human", control_user_id: "usr_other", control_user_name: "Ana Torres", flags: ["takeover"] }),
    });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByText(/Ana Torres holds this chat/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start Takeover" })).not.toBeInTheDocument();
  });

  it("reports a Takeover conflict from the server", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail(),
      [`POST /api/applications/:id/takeover`]: error(409, "control_held", "Pedro Ruiz holds this chat."),
    });
    renderRoute(`/applications/${ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "Start Takeover" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pedro Ruiz holds this chat.");
  });

  it("explains a Team text refused outside WhatsApp's 24-hour window and keeps it", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({ control: "human", control_user_id: "usr_rec_puebla", control_user_name: "Ana Torres", flags: ["takeover"] }),
      [`POST /api/applications/:id/takeover/messages`]: error(409, "outside_whatsapp_window", "Outside the window."),
    });
    renderRoute(`/applications/${ID}`);
    const box = await screen.findByLabelText("Message to the Candidate");
    fireEvent.change(box, { target: { value: "Hola Luis" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/more than 24 hours ago/);
    expect(box).toHaveValue("Hola Luis");
  });

  it("reflects a control change pushed by the stream", async () => {
    let holder: string | null = null;
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: () => detail({ control: holder ? "human" : "agent", control_user_name: holder }),
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("button", { name: "Start Takeover" });
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    holder = "Pedro Ruiz";
    act(() =>
      stream.emit("control", {
        type: "control",
        at: "2026-10-09T11:02:00Z",
        data: { control: "human", user_id: "usr_other", user_name: "Pedro Ruiz", since: "2026-10-09T11:02:00Z" },
      }),
    );
    expect(await screen.findByText(/Pedro Ruiz holds this chat/)).toBeInTheDocument();
  });

  it("refetches when the stream (re)opens, since the server does not replay what came before", async () => {
    const stored = detail();
    let messages = stored.messages;
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: () => ({ ...stored, messages }),
    });
    renderRoute(`/applications/${ID}`);
    const transcript = await screen.findByRole("log", { name: "Transcript" });
    const before = mock.find("GET", `/api/applications/${ID}`).length;
    // A message stored between the page's fetch and the stream's first open.
    messages = [...stored.messages, inbound];
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    act(() => stream.open());
    expect(await within(transcript).findByText("¡Perfecto, gracias!")).toBeInTheDocument();
    expect(mock.find("GET", `/api/applications/${ID}`).length).toBeGreaterThan(before);
  });

  it("lets an Operator hand back a Takeover someone else holds", async () => {
    let control: "agent" | "human" = "human";
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: () =>
        detail({
          control,
          control_user_id: control === "human" ? "usr_rec_puebla" : null,
          control_user_name: control === "human" ? "Recruiter Puebla (synthetic)" : null,
        }),
      [`POST /api/applications/:id/takeover/handback`]: () => {
        control = "agent";
        return { control: "agent", user_id: null, user_name: null, since: "2026-10-09T11:10:00Z" };
      },
    });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByText(/Recruiter Puebla \(synthetic\) holds this chat/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hand back to the Screening Agent" }));
    expect(await screen.findByRole("button", { name: "Start Takeover" })).toBeInTheDocument();
    // No summary typed: the server generates it from the transcript.
    expect(mock.find("POST", `/api/applications/${ID}/takeover/handback`)[0].body).toEqual({});
  });

  it("closes the stream when leaving the page", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    const view = renderRoute(`/applications/${ID}`);
    await screen.findByRole("button", { name: "Start Takeover" });
    view.unmount();
    expect(FakeEventSource.instances.every((s) => s.closed)).toBe(true);
  });
});

describe("Application detail: Approve (RT11)", () => {
  const POST = "POST /api/queue/approvals/:id";
  const actions = () => screen.getByRole("region", { name: "Actions" });
  const approvalItem = detail().open_queue_items[0];

  it("shows Approve in Actions as the one primary action, with Claim stepping down", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    const approve = await within(await screen.findByRole("region", { name: "Actions" })).findByRole("button", { name: "Approve" });
    expect(approve).toHaveClass("ui-btn--primary");
    const claim = within(actions()).getByRole("button", { name: "Claim" });
    expect(claim).toHaveClass("ui-btn--secondary");
    expect(actions().querySelectorAll(".ui-btn--primary")).toHaveLength(1);
  });

  it.each([
    ["no open approval", () => detail({ open_queue_items: [] })],
    ["only a resolved approval", () => detail({ open_queue_items: [{ ...approvalItem, status: "resolved" }] })],
    ["only a review", () => detail({ open_queue_items: [{ ...approvalItem, id: "qi_review_1", kind: "review" }] })],
    ["a closed Application", () => detail({ status: "closed", outcome: "rejection" })],
  ])("has no Approve button with %s", async (_what, make) => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: make() });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("region", { name: "Actions" });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("keeps Claim primary when there is nothing to approve", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ open_queue_items: [] }) });
    renderRoute(`/applications/${ID}`);
    expect(await within(await screen.findByRole("region", { name: "Actions" })).findByRole("button", { name: "Claim" })).toHaveClass("ui-btn--primary");
  });

  it("sends one POST with the same path and body the Review queue sends, and nothing for a second click", async () => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail(),
      [POST]: async () => {
        await wait;
        return { ...approvalItem, status: "resolved" };
      },
    });
    renderRoute(`/applications/${ID}`);
    const approve = await within(await screen.findByRole("region", { name: "Actions" })).findByRole("button", { name: "Approve" });
    fireEvent.click(approve);
    await waitFor(() => expect(approve).toBeDisabled());
    expect(approve).toHaveAttribute("aria-busy", "true");
    fireEvent.click(approve);
    fireEvent.click(approve);
    await act(async () => void (await new Promise((r) => setTimeout(r, 40))));
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")).toHaveLength(1);
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")[0].body).toEqual({ decision: "approved" });
    release();
    await act(async () => void (await new Promise((r) => setTimeout(r, 40))));
    expect(mock.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("shows the server's message in the alert on a 409 and keeps Approve", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail(),
      [POST]: error(409, "already_resolved", "Pedro Ruiz already resolved this Approval."),
    });
    renderRoute(`/applications/${ID}`);
    fireEvent.click(await within(await screen.findByRole("region", { name: "Actions" })).findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pedro Ruiz already resolved this Approval.");
    expect(within(actions()).getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("refreshes the three keys on success, then drops the button and moves focus to Actions", async () => {
    let approved = false;
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: () => detail({ open_queue_items: approved ? [] : [approvalItem] }),
      [POST]: () => {
        approved = true;
        return { ...approvalItem, status: "resolved" };
      },
    });
    const { client } = renderRoute(`/applications/${ID}`);
    const approve = await within(await screen.findByRole("region", { name: "Actions" })).findByRole("button", { name: "Approve" });
    const spy = vi.spyOn(client, "invalidateQueries");
    const before = mock.find("GET", `/api/applications/${ID}`).length;
    fireEvent.click(approve);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument());
    expect(actions()).toHaveAttribute("tabindex", "-1");
    expect(actions()).toHaveFocus();
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toEqual(expect.arrayContaining([JSON.stringify(["application", ID]), JSON.stringify(["applications"]), JSON.stringify(["queue"])]));
    expect(mock.find("GET", `/api/applications/${ID}`).length).toBe(before + 1);
    expect(mock.find("POST", "/api/queue/approvals/qi_approval_1")).toHaveLength(1);
  });
});

describe("Application detail: states (RT16)", () => {
  it("shows a Skeleton, not Loading text, while the Application loads", async () => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: async () => {
        await wait;
        return detail();
      },
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("navigation", { name: "Main" });
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    release();
    await screen.findByRole("log", { name: "Transcript" });
    expect(screen.queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
  });

  it("shows the ErrorState on a load error, and Retry asks for the Application once more", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/auth/me": recruiterMe,
      // The query retries twice, so the first three requests fail; the fourth, after Retry, succeeds.
      [`GET /api/applications/:id`]: () => (calls++ < 3 ? error(500, "internal", "The Application service is down.") : detail()),
    });
    const { client } = renderRoute(`/applications/${ID}`);
    client.setQueryDefaults(["application", ID], { retryDelay: 0 });
    expect(await screen.findByRole("alert", undefined, { timeout: 4000 })).toHaveTextContent("The Application service is down.");
    expect(screen.getByRole("link", { name: "Back to Applications" })).toHaveAttribute("href", "/applications");
    expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("log", { name: "Transcript" });
    expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(4);
  });

  it("keeps the 404 view: heading, sentence and back link, no Retry, no second request", async () => {
    const mock = mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: error(404, "not_found", "Application not found.") });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByRole("heading", { name: "Application not found" })).toBeInTheDocument();
    expect(screen.getByText("It does not exist, or it is outside your Locations.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Applications" })).toHaveAttribute("href", "/applications");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => void (await new Promise((r) => setTimeout(r, 40))));
    expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(1);
  });

  it("keeps the content on screen while a stream event refetches", async () => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: async () => {
        if (calls++ > 0) await wait;
        return detail();
      },
    });
    renderRoute(`/applications/${ID}`);
    const transcript = await screen.findByRole("log", { name: "Transcript" });
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    act(() =>
      stream.emit("control", {
        type: "control",
        at: "2026-10-09T11:02:00Z",
        data: { control: "agent", user_id: null, user_name: null, since: "2026-10-09T11:02:00Z" },
      }),
    );
    await waitFor(() => expect(calls).toBe(2));
    expect(screen.getByRole("log", { name: "Transcript" })).toBe(transcript);
    expect(screen.queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Actions" })).toBeInTheDocument();
    release();
    await act(async () => void (await new Promise((r) => setTimeout(r, 40))));
    expect(screen.getByRole("log", { name: "Transcript" })).toBe(transcript);
  });
});

describe("Application detail: status emphasis (RT20)", () => {
  it("marks the Stage once when it changes on a refetch, never on first load or an unchanged refetch", async () => {
    let stage: "qualified" | "approved" = "qualified";
    const mock = mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: () => detail({ stage }) });
    const { container } = renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    const frame = { type: "event", at: "2026-10-09T11:02:00Z", data: detail().events[0] };
    const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 40))));
    expect(container.querySelector("[data-emphasis]")).toBeNull();

    act(() => stream.emit("event", frame));
    await waitFor(() => expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(2));
    await settle();
    expect(container.querySelector("[data-emphasis]")).toBeNull();

    stage = "approved";
    act(() => stream.emit("event", frame));
    await waitFor(() => expect(container.querySelector("[data-emphasis]")).not.toBeNull());
    const marked = container.querySelector("[data-emphasis]") as HTMLElement;
    expect(container.querySelectorAll("[data-emphasis]")).toHaveLength(1);
    expect(marked.tagName).toBe("SPAN");
    expect(marked).toHaveClass("today-emphasis");
    expect(marked).toHaveTextContent("Approved");

    act(() => stream.emit("event", frame));
    await waitFor(() => expect(mock.find("GET", `/api/applications/${ID}`)).toHaveLength(4));
    await settle();
    expect(container.querySelector("[data-emphasis]")).toBe(marked);
    expect(container.querySelectorAll("[data-emphasis]")).toHaveLength(1);
  });
});

describe("Application detail: step 7 fixes", () => {
  const POST = "POST /api/queue/approvals/:id";

  it("says Approved <name> in a polite status inside Actions after Approve, with focus kept on Actions", async () => {
    let approved = false;
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: () => detail({ open_queue_items: approved ? [] : detail().open_queue_items }),
      [POST]: () => {
        approved = true;
        return { ...detail().open_queue_items[0], status: "resolved" };
      },
    });
    renderRoute(`/applications/${ID}`);
    const actions = await screen.findByRole("region", { name: "Actions" });
    expect(within(actions).getByRole("status")).toHaveTextContent("");
    fireEvent.click(within(actions).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(within(actions).getByRole("status")).toHaveTextContent("Approved Luis Hernández"));
    expect(within(actions).getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(actions).toHaveFocus();
  });

  it("shows no flag placeholder in the head when there are no flags, and the tags when there are", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ flags: [] }) });
    const first = renderRoute(`/applications/${ID}`);
    const head = (await screen.findByRole("heading", { level: 1 })).closest(".page-head") as HTMLElement;
    expect(within(head).queryByText("-")).not.toBeInTheDocument();
    first.unmount();
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ flags: ["takeover"] }) });
    renderRoute(`/applications/${ID}`);
    const head2 = (await screen.findByRole("heading", { level: 1 })).closest(".page-head") as HTMLElement;
    expect(within(head2).getByText("Takeover")).toBeInTheDocument();
  });

  it("names the Candidate in the breadcrumb, not the raw id", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    const crumb = (await screen.findByText("Luis Hernández", { selector: "span[title]" })).parentElement as HTMLElement;
    expect(crumb).toHaveTextContent("Applications / Luis Hernández");
    expect(crumb).not.toHaveTextContent(ID);
  });

  it("labels the timeline's actors in plain words and names the Stage or field an entry carries", async () => {
    const base = detail();
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({
        events: [
          { id: 1, kind: "answer_recorded", actor: "candidate", user_name: null, data: { field: "zone" }, created_at: "2026-10-09T10:00:50Z" },
          { id: 2, kind: "stage_changed", actor: "agent", user_name: null, data: { from: "greeting", to: "name" }, created_at: "2026-10-09T10:01:00Z" },
          { id: 3, kind: "claim_taken", actor: "user", user_name: "Ana Torres", data: {}, created_at: "2026-10-09T10:02:00Z" },
          { id: 4, kind: "stalled", actor: "system", user_name: null, data: {}, created_at: "2026-10-09T10:03:00Z" },
          { id: 5, kind: "stage_changed", actor: "agent", user_name: null, data: { note: "no codes" }, created_at: "2026-10-09T10:04:00Z" },
          ...base.events.slice(0, 0),
        ],
      }),
    });
    renderRoute(`/applications/${ID}`);
    const timeline = (await screen.findByRole("heading", { name: "Timeline" })).closest("section")!;
    const items = within(timeline).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Answer recorded");
    expect(items[0]).toHaveTextContent("Zone");
    expect(items[0]).toHaveTextContent("Candidate ·");
    expect(items[1]).toHaveTextContent("Stage changed");
    expect(items[1]).toHaveTextContent("Greeting → Name");
    expect(items[1]).toHaveTextContent("Screening Agent ·");
    expect(items[2]).toHaveTextContent("Ana Torres ·");
    expect(items[3]).toHaveTextContent("System ·");
    expect(items[4]).not.toHaveTextContent("→");
  });

  it("keeps the decision actions apart from Reject and Export JSON, in the same order", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    const actions = await screen.findByRole("region", { name: "Actions" });
    const approve = within(actions).getByRole("button", { name: "Approve" });
    const claim = within(actions).getByRole("button", { name: "Claim" });
    const reject = within(actions).getByRole("button", { name: "Reject..." });
    const exportLink = within(actions).getByRole("link", { name: "Export JSON" });
    const group = (el: HTMLElement) => el.closest(".detail-actions__group");
    expect(group(approve)).not.toBeNull();
    expect(group(approve)).toBe(group(claim));
    expect(group(reject)).not.toBeNull();
    expect(group(reject)).toBe(group(exportLink));
    expect(group(reject)).not.toBe(group(approve));
    const follows = (a: HTMLElement, b: HTMLElement) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(follows(approve, claim)).toBeTruthy();
    expect(follows(claim, reject)).toBeTruthy();
    expect(follows(reject, exportLink)).toBeTruthy();
  });

  it("says what each duration measures", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    renderRoute(`/applications/${ID}`);
    expect(await screen.findByText("Time in Screening at summary")).toBeInTheDocument();
    const steps = screen.getByRole("heading", { name: "Time per step" }).closest("section")!;
    expect(within(steps).getByText("Elapsed since start")).toBeInTheDocument();
    expect(within(steps).queryByText("Total")).not.toBeInTheDocument();
  });
});

describe("Application detail: timeline meta (step 7, owner fixes)", () => {
  it("keeps the detail and the actor in separate elements, the actor on its own muted line", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({
        events: [{ id: 2, kind: "stage_changed", actor: "agent", user_name: null, data: { from: "greeting", to: "name" }, created_at: "2026-10-09T10:01:00Z" }],
      }),
    });
    renderRoute(`/applications/${ID}`);
    const timeline = (await screen.findByRole("heading", { name: "Timeline" })).closest("section")!;
    const item = within(timeline).getByRole("listitem");
    const detailText = within(item).getByText("Greeting → Name");
    const meta = within(item).getByText(/Screening Agent ·/);
    expect(meta).not.toBe(detailText);
    expect(meta).toHaveClass("detail-timeline__meta");
    expect(meta).not.toContainElement(detailText);
    expect(detailText).not.toHaveTextContent("Screening Agent");
    expect(meta).not.toHaveTextContent("→");
  });
});

describe("Application detail: header strip (UI5)", () => {
  it("shows the time in stage, labelled In stage, with the formatted value", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ time_in_stage_seconds: 3720 }) });
    renderRoute(`/applications/${ID}`);
    const strip = (await screen.findByRole("heading", { level: 1 })).closest(".detail-strip") as HTMLElement;
    expect(strip).not.toBeNull();
    expect(strip).toHaveTextContent("In stage 1h 2m");
  });

  it("holds every strip element in one container, placed before the columns", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ flags: ["takeover"] }) });
    renderRoute(`/applications/${ID}`);
    const h1 = await screen.findByRole("heading", { level: 1 });
    const strip = h1.closest(".detail-strip") as HTMLElement;
    expect(strip).not.toBeNull();
    expect(within(strip).getByText("Luis Hernández", { selector: "span[title]" })).toBeInTheDocument();
    expect(h1.closest(".page-head")).not.toBeNull();
    expect(strip).toContainElement(h1.closest(".page-head") as HTMLElement);
    expect(within(h1.closest(".page-head") as HTMLElement).getByText("Takeover")).toBeInTheDocument();
    expect(strip).toHaveTextContent("Qualified");
    expect(strip).toContainElement(screen.getByRole("region", { name: "Actions" }));
    const follows = (a: Element, b: Element) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
    for (const name of ["Summary", "Transcript", "Timeline"]) {
      const card = screen.getByRole("region", { name });
      expect(strip).not.toContainElement(card);
      expect(follows(strip, card)).toBeTruthy();
    }
  });
});

describe("Application detail: columns (UI6)", () => {
  it("roots the page in detail-page and orders strip, column 1, column 2, column 3 in the DOM", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail() });
    const { container } = renderRoute(`/applications/${ID}`);
    await screen.findByRole("log", { name: "Transcript" });
    const root = container.querySelector(".detail-page") as HTMLElement;
    expect(root).not.toBeNull();
    expect(root).toHaveClass("stack");
    const follows = (a: Element, b: Element) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
    const summary = screen.getByRole("region", { name: "Summary" });
    const answers = screen.getByRole("region", { name: "Answers" });
    const transcript = screen.getByRole("region", { name: "Transcript" });
    const takeover = screen.getByRole("region", { name: "Takeover" });
    const timeline = screen.getByRole("region", { name: "Timeline" });
    expect(follows(summary, answers)).toBeTruthy();
    expect(follows(answers, transcript)).toBeTruthy();
    expect(follows(transcript, takeover)).toBeTruthy();
    expect(follows(takeover, timeline)).toBeTruthy();
    expect(follows(screen.getByRole("region", { name: "Candidate's other Applications" }), transcript)).toBeTruthy();
  });

  it("jumps the Transcript to its newest message by setting the log's scrollTop, never with scrollIntoView", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: scrollIntoView, configurable: true, writable: true });
    try {
      let messages = detail().messages;
      mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: () => detail({ messages }) });
      renderRoute(`/applications/${ID}`);
      const log = await screen.findByRole("log", { name: "Transcript" });
      Object.defineProperty(log, "scrollHeight", { value: 500, configurable: true });
      const next: Message = {
        id: 44,
        direction: "inbound",
        sender: "candidate",
        sender_name: null,
        content_type: "text",
        body: "¿Sigue abierta la vacante?",
        interactive: null,
        created_at: "2026-10-09T11:05:00Z",
      };
      messages = [...messages, next];
      const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
      act(() => stream.emit("message", { type: "message", at: next.created_at, data: next }));
      expect(await within(log).findByText("¿Sigue abierta la vacante?")).toBeInTheDocument();
      await waitFor(() => expect(log.scrollTop).toBe(500));
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });
});

describe("Application detail: compact Timeline (UI7)", () => {
  const events = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: i + 1,
      kind: "claim_taken" as const,
      actor: "user" as const,
      user_name: `User ${i + 1}`,
      data: {},
      created_at: `2026-10-09T10:${String(i).padStart(2, "0")}:00Z`,
    }));
  const timeline = async () => (await screen.findByRole("heading", { name: "Timeline" })).closest("section")!;

  it("shows the latest 10 of 12 events oldest first, and Show all reveals all 12", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ events: events(12) }) });
    renderRoute(`/applications/${ID}`);
    const region = await timeline();
    const items = within(region).getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[0]).toHaveTextContent("User 3 ·");
    expect(items[9]).toHaveTextContent("User 12 ·");
    const button = within(region).getByRole("button", { name: "Show all 12 events" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    const list = region.querySelector("ol") as HTMLElement;
    expect(list.id).not.toBe("");
    expect(button).toHaveAttribute("aria-controls", list.id);
    fireEvent.click(button);
    expect(within(region).getAllByRole("listitem")).toHaveLength(12);
    expect(within(region).getAllByRole("listitem")[0]).toHaveTextContent("User 1 ·");
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAccessibleName("Show latest 10");
    fireEvent.click(button);
    expect(within(region).getAllByRole("listitem")).toHaveLength(10);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("shows no Show all control with 10 events", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: detail({ events: events(10) }) });
    renderRoute(`/applications/${ID}`);
    const region = await timeline();
    expect(within(region).getAllByRole("listitem")).toHaveLength(10);
    expect(within(region).queryByRole("button")).not.toBeInTheDocument();
  });

  it("lets a streamed 11th event join the window, dropping the oldest shown, and counts it", async () => {
    let list = events(10);
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: () => detail({ events: list }) });
    renderRoute(`/applications/${ID}`);
    const region = await timeline();
    expect(within(region).getAllByRole("listitem")[0]).toHaveTextContent("User 1 ·");
    const eleventh = events(11)[10];
    list = [...list, eleventh];
    const stream = FakeEventSource.instances.find((s) => s.url === `/api/applications/${ID}/stream`)!;
    act(() => stream.emit("event", { type: "event", at: eleventh.created_at, data: eleventh }));
    expect(await within(region).findByRole("button", { name: "Show all 11 events" })).toHaveAttribute("aria-expanded", "false");
    const items = within(region).getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[0]).toHaveTextContent("User 2 ·");
    expect(items[9]).toHaveTextContent("User 11 ·");
  });
});

describe("Application detail: full text in title where a line truncates (critique round 1)", () => {
  it("carries the Timeline meta, the Candidate's words and the Locations line in title, text unchanged", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      [`GET /api/applications/:id`]: detail({
        events: [{ id: 2, kind: "stage_changed", actor: "agent", user_name: null, data: { from: "greeting", to: "name" }, created_at: "2026-10-09T10:01:00Z" }],
      }),
    });
    renderRoute(`/applications/${ID}`);
    const timeline = (await screen.findByRole("heading", { name: "Timeline" })).closest("section")!;
    const meta = within(timeline).getByText(/Screening Agent ·/);
    expect(meta.getAttribute("title")).toBe(meta.textContent);

    const answers = screen.getByRole("heading", { name: "Answers" }).closest("section")!;
    const words = within(answers).getByText("sí, tengo licencia");
    expect(words).toHaveAttribute("title", "sí, tengo licencia");

    const actions = screen.getByRole("region", { name: "Actions" });
    const where = actions.querySelector(".detail-actions__where") as HTMLElement;
    expect(where).not.toBeNull();
    expect(where.getAttribute("title")).toBe(where.textContent);
  });
});

describe("Application detail: summary prose state (ADR 0072)", () => {
  const withState = (prose_status?: string) => {
    const base = detail();
    const structured = { ...base.summary!.structured, ...(prose_status ? { prose_status } : {}) };
    return detail({ summary: { ...base.summary!, prose: null, structured } });
  };

  it("says the written summary is being retried", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: withState("retrying") });
    renderRoute(`/applications/${ID}`);
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(await within(summary).findByRole("status")).toHaveTextContent("It is retried automatically");
  });

  it("says the written summary could not be produced", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: withState("failed") });
    renderRoute(`/applications/${ID}`);
    const summary = await screen.findByRole("region", { name: "Summary" });
    expect(await within(summary).findByRole("status")).toHaveTextContent("could not be produced");
  });

  it("says nothing when there is prose, or no state", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: withState() });
    renderRoute(`/applications/${ID}`);
    const summary = await screen.findByRole("region", { name: "Summary" });
    await screen.findByRole("log", { name: "Transcript" });
    expect(within(summary).queryByText(/written summary/)).not.toBeInTheDocument();
  });
});
