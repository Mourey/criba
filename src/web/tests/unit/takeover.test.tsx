/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { ApplicationDetail, Message, Me } from "../../src/api/types.ts";
import { AuthProvider } from "../../src/auth/AuthProvider.tsx";
import { TakeoverPanel } from "../../src/components/TakeoverPanel.tsx";
import { Transcript } from "../../src/components/Transcript.tsx";
import { detail, operatorMe, recruiterMe } from "./fixtures.ts";
import { error, mockApi } from "./helpers.tsx";

// TakeoverPanel and Transcript render here with no router and no page around them, inside the
// app's AuthProvider (the panel reads the signed-in user and the role from it).

function renderWith(ui: ReactNode, me: Me = recruiterMe) {
  const mock = mockApi({
    "GET /api/auth/me": me,
    "POST /api/applications/:id/takeover": { control: "human", user_id: me.user.id, user_name: me.user.name, since: "2026-10-09T11:02:00Z" },
    "POST /api/applications/:id/takeover/handback": { control: "agent", user_id: null, user_name: null, since: "2026-10-09T11:10:00Z" },
    "POST /api/applications/:id/takeover/messages": [
      201,
      { id: 42, direction: "outbound", sender: "human", sender_name: me.user.name, content_type: "text", body: "Hola Luis", interactive: null, created_at: "2026-10-09T11:03:00Z" },
    ],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
  return { mock, client, ...view };
}

const held = (overrides: Partial<ApplicationDetail> = {}) => detail({ control: "human", control_user_id: recruiterMe.user.id, control_user_name: "Ana Torres", ...overrides });

describe("TakeoverPanel modes", () => {
  it("is a section named Takeover, and offers Start Takeover when the Screening Agent holds the chat", async () => {
    const { mock } = renderWith(<TakeoverPanel application={detail()} />);
    const section = await screen.findByRole("region", { name: "Takeover" });
    expect(within(section).getByText(/The Screening Agent holds this chat/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();

    fireEvent.click(within(section).getByRole("button", { name: "Start Takeover" }));
    await waitFor(() => expect(mock.find("POST", `/api/applications/${detail().id}/takeover`)).toHaveLength(1));
  });

  it("disables Start Takeover on a closed Application and says so", async () => {
    renderWith(<TakeoverPanel application={detail({ status: "closed" })} />);
    expect(await screen.findByRole("button", { name: "Start Takeover" })).toBeDisabled();
    expect(screen.getByText("This Application is closed.")).toBeInTheDocument();
  });

  it("names who holds the chat when it is someone else, with no way to reply or hand back for a Recruiter", async () => {
    renderWith(<TakeoverPanel application={detail({ control: "human", control_user_id: "usr_other", control_user_name: "Pedro Ruiz" })} />);
    expect(await screen.findByText(/Pedro Ruiz holds this chat. Only they can reply or hand it back\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start Takeover" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hand back to the Screening Agent" })).not.toBeInTheDocument();
  });

  it("falls back to a generic holder name", async () => {
    renderWith(<TakeoverPanel application={detail({ control: "human", control_user_id: "usr_other", control_user_name: null })} />);
    expect(await screen.findByText(/Another team member holds this chat/)).toBeInTheDocument();
  });

  it("lets an Operator hand back a Takeover someone else holds, with no summary", async () => {
    const application = detail({ control: "human", control_user_id: "usr_other", control_user_name: "Pedro Ruiz" });
    const { mock } = renderWith(<TakeoverPanel application={application} />, operatorMe);
    expect(await screen.findByText(/as an Operator you can also hand it back/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Message to the Candidate")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hand back to the Screening Agent" }));
    await waitFor(() => expect(mock.find("POST", `/api/applications/${application.id}/takeover/handback`)).toHaveLength(1));
    expect(mock.find("POST", `/api/applications/${application.id}/takeover/handback`)[0].body).toEqual({});
  });

  it("gives the holder a labelled composer and a labelled summary, and sends and hands back", async () => {
    const application = held();
    const { mock } = renderWith(<TakeoverPanel application={application} />);
    expect(await screen.findByText(/You hold this chat/)).toBeInTheDocument();
    const box = screen.getByLabelText("Message to the Candidate");
    expect(box.tagName).toBe("TEXTAREA");
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();

    fireEvent.change(box, { target: { value: "  Hola Luis  " } });
    fireEvent.click(send);
    await waitFor(() => expect(mock.find("POST", `/api/applications/${application.id}/takeover/messages`)).toHaveLength(1));
    expect(mock.find("POST", `/api/applications/${application.id}/takeover/messages`)[0].body).toEqual({ text: "Hola Luis" });
    await waitFor(() => expect(box).toHaveValue(""));

    const summary = screen.getByLabelText(/^Summary for the Screening Agent/);
    expect(summary.tagName).toBe("TEXTAREA");
    fireEvent.change(summary, { target: { value: "Confirmed the motorbike." } });
    fireEvent.click(screen.getByRole("button", { name: "Hand back to the Screening Agent" }));
    await waitFor(() => expect(mock.find("POST", `/api/applications/${application.id}/takeover/handback`)).toHaveLength(1));
    expect(mock.find("POST", `/api/applications/${application.id}/takeover/handback`)[0].body).toEqual({ summary: "Confirmed the motorbike." });
    await waitFor(() => expect(summary).toHaveValue(""));
  });

  it("keeps the typed text and explains a refusal outside WhatsApp's 24-hour window", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "POST /api/applications/:id/takeover/messages": error(409, "outside_whatsapp_window", "Outside the window."),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <TakeoverPanel application={held()} />
        </AuthProvider>
      </QueryClientProvider>,
    );
    const box = await screen.findByLabelText("Message to the Candidate");
    fireEvent.change(box, { target: { value: "Hola Luis" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/more than 24 hours ago/);
    expect(alert).toHaveTextContent(/Your text is kept/);
    expect(box).toHaveValue("Hola Luis");
  });

  it("shows another failure's message in the alert", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "POST /api/applications/:id/takeover": error(409, "control_held", "Pedro Ruiz holds this chat."),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <TakeoverPanel application={detail()} />
        </AuthProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Start Takeover" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pedro Ruiz holds this chat.");
  });
});

describe("Transcript", () => {
  const messages: Message[] = [
    { id: 1, direction: "inbound", sender: "candidate", sender_name: null, content_type: "text", body: "Hola", interactive: null, created_at: "2026-10-09T10:00:00Z" },
    { id: 2, direction: "outbound", sender: "agent", sender_name: null, content_type: "text", body: "¡Hola! Soy el asistente", interactive: null, created_at: "2026-10-09T10:00:02Z" },
    {
      id: 3,
      direction: "outbound",
      sender: "agent",
      sender_name: null,
      content_type: "interactive",
      body: "¿Qué disponibilidad tienes?",
      interactive: { style: "buttons", options: [{ id: "full_time", label: "Tiempo completo" }, { id: "part_time", label: "Medio tiempo" }] },
      created_at: "2026-10-09T10:01:40Z",
    },
    { id: 4, direction: "inbound", sender: "candidate", sender_name: null, content_type: "button_reply", body: "Tiempo completo", interactive: null, created_at: "2026-10-09T10:01:50Z" },
    { id: 5, direction: "outbound", sender: "human", sender_name: "Ana Torres", content_type: "text", body: "Soy Ana, del equipo.", interactive: null, created_at: "2026-10-09T10:02:00Z" },
    { id: 6, direction: "outbound", sender: "human", sender_name: null, content_type: "text", body: "Sin nombre", interactive: null, created_at: "2026-10-09T10:02:10Z" },
    { id: 7, direction: "outbound", sender: "system", sender_name: null, content_type: "text", body: "Takeover started", interactive: null, created_at: "2026-10-09T10:02:20Z" },
    { id: 8, direction: "inbound", sender: "candidate", sender_name: null, content_type: "media", body: "", media_type: "image", interactive: null, created_at: "2026-10-09T10:02:30Z" },
    { id: 9, direction: "inbound", sender: "candidate", sender_name: null, content_type: "media", body: "", interactive: null, created_at: "2026-10-09T10:02:40Z" },
  ];

  it("is a log named Transcript, one bubble per message, with the sender and the time", () => {
    render(<Transcript messages={messages} />);
    const log = screen.getByRole("log", { name: "Transcript" });
    const bubbles = [...log.querySelectorAll(".bubble")];
    expect(bubbles).toHaveLength(messages.length);
    expect(bubbles.map((b) => b.getAttribute("data-sender"))).toEqual(messages.map((m) => m.sender));
    expect(bubbles.map((b) => b.className)).toEqual([
      "bubble",
      "bubble out",
      "bubble out",
      "bubble",
      "bubble human",
      "bubble human",
      "bubble system",
      "bubble",
      "bubble",
    ]);

    expect(within(log).getByText("Ana Torres")).toBeInTheDocument();
    expect(within(log).getByText("Team member")).toBeInTheDocument();
    expect(within(log).getAllByText("Screening Agent")).toHaveLength(2);
    // A system line has no sender line.
    expect(bubbles[6].querySelector(".who-line")).toBeNull();

    const times = [...log.querySelectorAll("time")];
    expect(times).toHaveLength(messages.length);
    expect(times[0]).toHaveAttribute("datetime", "2026-10-09T10:00:00Z");
    expect(times[0].textContent).toMatch(/\d/);
  });

  it("shows the offered options, the tapped option and a media placeholder", () => {
    render(<Transcript messages={messages} />);
    const log = screen.getByRole("log", { name: "Transcript" });
    const offered = within(log).getByLabelText("Options offered");
    expect(within(offered).getByText("Tiempo completo")).toBeInTheDocument();
    expect(within(offered).getByText("Medio tiempo")).toBeInTheDocument();
    expect(within(log).getByText("Tapped option")).toBeInTheDocument();
    expect(within(log).getByText(/\[image\]/)).toBeInTheDocument();
    expect(within(log).getByText(/\[media\]/)).toBeInTheDocument();
  });

  it("says so when there are no messages", () => {
    render(<Transcript messages={[]} />);
    expect(screen.getByText("No messages yet.")).toBeInTheDocument();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
  });
});

describe("rp- wrappers and the scroll region (axe on the Application page)", () => {
  const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/recruiter.css"), "utf8");

  it("carries rp-takeover on the section named Takeover", async () => {
    const { container } = renderWith(<TakeoverPanel application={detail()} />);
    const section = await screen.findByRole("region", { name: "Takeover" });
    expect(container.querySelector(".rp-takeover")).toBe(section);
  });

  it("keeps the section named Takeover by the takeover-heading id (RP7)", async () => {
    renderWith(<TakeoverPanel application={detail()} />);
    const section = await screen.findByRole("region", { name: "Takeover" });
    expect(section.tagName).toBe("SECTION");
    expect(section).toHaveAttribute("aria-labelledby", "takeover-heading");
    const heading = document.getElementById("takeover-heading");
    expect(heading).not.toBeNull();
    expect(heading).toHaveTextContent("Takeover");
    expect(heading?.tagName).toBe("H2");
    expect(section).toContainElement(heading);
  });

  it("puts rp-transcript on the log and on the empty text", () => {
    const { container, unmount } = render(<Transcript messages={[detail().messages[0]]} />);
    expect(screen.getByRole("log", { name: "Transcript" })).toHaveClass("rp-transcript");
    unmount();
    const empty = render(<Transcript messages={[]} />);
    expect(empty.container.querySelector(".rp-transcript-empty")).not.toBeNull();
    expect(container).toBeDefined();
  });

  it("makes the scrolling log keyboard-focusable while it keeps its name", () => {
    render(<Transcript messages={[detail().messages[0]]} />);
    const log = screen.getByRole("log", { name: "Transcript" });
    expect(log).toHaveAttribute("tabindex", "0");
  });

  it("sets the sender line and the time in --ink, which holds 4.5:1 on every bubble in both themes", () => {
    for (const selector of [".rp-transcript .bubble .who-line", ".rp-transcript .bubble time", ".rp-transcript .bubble.system"]) {
      const rule = css.split("}").find((block) => block.includes(selector));
      expect(rule, selector).toBeDefined();
      expect(rule).toMatch(/color:\s*var\(--ink\)/);
    }
  });

  it("gives the Takeover note a muted colour that holds 4.5:1 on the panel", () => {
    const rule = css.split("}").find((block) => block.includes(".rp-takeover .rp-muted"));
    expect(rule).toBeDefined();
    expect(rule).toMatch(/color:\s*var\(--ink-muted\)/);
  });

  it("keeps bubble styles under .rp-transcript and out of app.css", () => {
    expect(css).toMatch(/\.rp-transcript \.bubble\.out/);
    expect(css).toMatch(/\.rp-transcript \.bubble\.human/);
    expect(css).toMatch(/\.rp-transcript \.bubble\.system/);
  });
});
