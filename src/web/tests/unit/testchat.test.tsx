import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../../src/api/types.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// The voice call's tests live in voice.test.tsx: voice is its own page, /voice (ADR 0059, owner decision 65).

const CONV = "wc_9f2c7e1b4a6d4e0f8b3a5c7d9e1f2a3b";

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

const hello = msg({ id: 13, direction: "inbound", sender: "candidate", text: "Hola, soy Ana López" });
const question = msg({
  id: 14,
  direction: "outbound",
  sender: "agent",
  sender_label: "Sazón Empleo",
  text: "¿Tienes carnet de conducir?",
  interactive: { style: "buttons", options: [{ id: "yes", label: "Sí" }, { id: "no", label: "No" }] },
});

/** A read endpoint that returns whatever messages are "stored" so far, like the real long poll. */
function chatApi(store: ChatMessage[], extra: Record<string, unknown> = {}) {
  return mockApi({
    "POST /api/chat/conversations": [201, { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0 }],
    "GET /api/chat/conversations/:id/messages": (call) => {
      const after = Number(call.query.get("after") ?? 0);
      const messages = store.filter((m) => m.id > after);
      // run_finished is required in the contract and always sent by the backend.
      return { cursor: store.at(-1)?.id ?? 0, agent_typing: false, run_finished: true, messages };
    },
    "POST /api/chat/conversations/:id/messages": (call) => {
      const body = call.body as { message_id: string; text: string };
      store.push(msg({ id: 13, direction: "inbound", sender: "candidate", message_id: body.message_id, text: body.text }));
      store.push(question);
      return [202, { message_id: body.message_id, id: 13, duplicate: false }];
    },
    "POST /api/chat/conversations/:id/replies": [202, { message_id: "x", id: 16, duplicate: false }],
    ...extra,
  });
}

describe("Test chat page", () => {
  it("starts a conversation with the chosen country and language, and needs no login", async () => {
    const mock = chatApi([]);
    renderRoute("/test-chat");
    fireEvent.change(await screen.findByLabelText("Country"), { target: { value: "ES" } });
    expect(screen.getByLabelText("Language")).toHaveValue("es-ES");
    fireEvent.change(screen.getByLabelText(/Phone/), { target: { value: "+34612345678" } });
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));

    expect(await screen.findByRole("log", { name: "Messages" })).toBeInTheDocument();
    expect(mock.find("POST", "/api/chat/conversations")[0].body).toEqual({
      country: "ES",
      locale: "es-ES",
      phone: "+34612345678",
      client: "test_chat",
    });
    expect(mock.find("GET", "/api/auth/me")).toHaveLength(0);
  });

  it("sends a message with a client-generated message_id and shows the agent's reply", async () => {
    const store: ChatMessage[] = [];
    const mock = chatApi(store);
    renderRoute("/test-chat");
    fireEvent.click(await screen.findByRole("button", { name: "Start conversation" }));
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "Hola, soy Ana López" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const log = screen.getByRole("log", { name: "Messages" });
    expect(await within(log).findByText("¿Tienes carnet de conducir?")).toBeInTheDocument();
    expect(within(log).getByText("Hola, soy Ana López")).toBeInTheDocument();
    expect(within(log).getByText("Sazón Empleo")).toBeInTheDocument();

    const sent = mock.find("POST", `/api/chat/conversations/${CONV}/messages`)[0].body as { message_id: string; text: string };
    expect(sent.text).toBe("Hola, soy Ana López");
    expect(sent.message_id.length).toBeGreaterThan(8);
  });

  it("resends a failed message with the same message_id, so a stored one is not run twice", async () => {
    const store: ChatMessage[] = [];
    let attempts = 0;
    const mock = chatApi(store, {
      "POST /api/chat/conversations/:id/messages": (call: { body: unknown }) => {
        attempts += 1;
        const body = call.body as { message_id: string; text: string };
        // The first attempt is stored but its answer is lost; the server dedupes the resend.
        if (attempts === 1) return error(500, "internal", "Unexpected error.");
        return [202, { message_id: body.message_id, id: 13, duplicate: attempts === 2 }];
      },
    });
    renderRoute("/test-chat");
    fireEvent.click(await screen.findByRole("button", { name: "Start conversation" }));
    const input = await screen.findByLabelText("Message");
    fireEvent.change(input, { target: { value: "Hola, soy Ana López" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected error.");
    // The page puts the unsent text back; sending it again is a retry of the same message.
    await waitFor(() => expect(input).toHaveValue("Hola, soy Ana López"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(mock.find("POST", `/api/chat/conversations/${CONV}/messages`)).toHaveLength(2));
    await waitFor(() => expect(input).toHaveValue(""));

    fireEvent.change(input, { target: { value: "Otro mensaje" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(mock.find("POST", `/api/chat/conversations/${CONV}/messages`)).toHaveLength(3));

    const ids = mock.find("POST", `/api/chat/conversations/${CONV}/messages`).map((c) => (c.body as { message_id: string }).message_id);
    expect(ids[1]).toBe(ids[0]);
    expect(ids[2]).not.toBe(ids[0]);
  });

  it("reads after the cursor with a long poll", async () => {
    const store = [hello, question];
    const mock = chatApi(store);
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    await screen.findByText("¿Tienes carnet de conducir?");
    const first = mock.find("GET", `/api/chat/conversations/${CONV}/messages`)[0];
    expect(first.query.get("after")).toBe("0");
    expect(first.query.get("wait")).toBe("20");
  });

  it("taps an option, which sends a reply to that interactive message, and only the latest options are live", async () => {
    const older = msg({ id: 10, direction: "outbound", sender: "agent", text: "¿Cómo te llamas?", interactive: { style: "buttons", options: [{ id: "skip", label: "Omitir" }] } });
    const mock = chatApi([older, question]);
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    await screen.findByText("¿Tienes carnet de conducir?");

    expect(screen.getByRole("button", { name: "Omitir" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Sí" }));
    await waitFor(() => expect(mock.find("POST", `/api/chat/conversations/${CONV}/replies`)).toHaveLength(1));
    const body = mock.find("POST", `/api/chat/conversations/${CONV}/replies`)[0].body as Record<string, unknown>;
    expect(body).toMatchObject({ in_reply_to: 14, option_id: "yes" });
    expect(typeof body.message_id).toBe("string");
  });

  it("shows the server's Spanish message when the options expired", async () => {
    chatApi([question], {
      "POST /api/chat/conversations/:id/replies": error(409, "option_expired", "Esas opciones ya no están disponibles. Escribe tu respuesta, por favor."),
    });
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    fireEvent.click(await screen.findByRole("button", { name: "Sí" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Esas opciones ya no están disponibles.");
  });

  it("shows team members as the Client's team and links the .ics attachment", async () => {
    chatApi([
      msg({ id: 20, direction: "outbound", sender: "human", sender_label: "Equipo de Grupo Sazón", text: "Hola, soy del equipo." }),
      msg({ id: 21, direction: "outbound", sender: "agent", sender_label: "Sazón Empleo", text: "Tu entrevista quedó.", attachments: [{ type: "ics", url: "/api/chat/x/invite.ics", filename: "entrevista.ics" }] }),
    ]);
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    expect(await screen.findByText("Equipo de Grupo Sazón")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "entrevista.ics" })).toHaveAttribute("href", "/api/chat/x/invite.ics");
  });

  it("shows the agent's name and the conversation's language, and follows a language change", async () => {
    let language: string | null = "es-MX";
    chatApi([hello], {
      "POST /api/chat/conversations": [
        201,
        { conversation_id: CONV, created_at: "2026-10-09T10:00:00Z", cursor: 0, persona_name: "Sazón Empleo", language: "es-MX" },
      ],
      "GET /api/chat/conversations/:id/messages": () => ({
        cursor: 13,
        agent_typing: false,
        run_finished: true,
        persona_name: "Sazón Empleo",
        language,
        messages: [],
      }),
    });
    renderRoute("/test-chat");
    fireEvent.click(await screen.findByRole("button", { name: "Start conversation" }));
    const meta = await screen.findByTestId("chat-meta");
    expect(meta).toHaveTextContent("Agent: Sazón Empleo");
    expect(meta).toHaveTextContent("Language: Spanish (Mexico)");

    language = "en";
    fireEvent.change(await screen.findByLabelText("Message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByTestId("chat-meta")).toHaveTextContent("Language: English"));
  });

  it("shows typing until run_finished, also while the run is only queued", async () => {
    let page = { agent_typing: false, run_finished: false };
    chatApi([hello], {
      "GET /api/chat/conversations/:id/messages": () => ({ cursor: 13, ...page, messages: [hello] }),
    });
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    // Queued: no run in progress yet, but the agent's turn is not over.
    expect(await screen.findByText("Typing...")).toBeInTheDocument();
    page = { agent_typing: true, run_finished: false };
    await waitFor(() => expect(screen.getByText("Typing...")).toBeInTheDocument());
    page = { agent_typing: false, run_finished: true };
    await waitFor(() => expect(screen.queryByText("Typing...")).not.toBeInTheDocument(), { timeout: 4000 });
  });

  it("shows nothing extra when the channel sends neither field", async () => {
    chatApi([]);
    renderRoute("/test-chat");
    fireEvent.click(await screen.findByRole("button", { name: "Start conversation" }));
    await screen.findByRole("log", { name: "Messages" });
    expect(screen.queryByTestId("chat-meta")).not.toBeInTheDocument();
  });

  it("goes back to the start form when the conversation no longer exists", async () => {
    mockApi({ "GET /api/chat/conversations/:id/messages": error(404, "not_found", "Conversation not found.") });
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    expect(await screen.findByRole("alert")).toHaveTextContent("That conversation no longer exists.");
    expect(screen.getByRole("button", { name: "Start conversation" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("criba.testChat.conversation")).toBeNull();
  });

  it("is text only: no voice control, notice or call status (owner decision 65)", async () => {
    const mock = chatApi([hello, question]);
    window.sessionStorage.setItem("criba.testChat.conversation", CONV);
    renderRoute("/test-chat");
    await screen.findByText("¿Tienes carnet de conducir?");
    expect(screen.queryByRole("button", { name: /^(Hablar|Colgar)$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Llamada de voz" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(/asistente de IA/)).not.toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Messages" })).toHaveAttribute("aria-live", "polite");
    expect(mock.calls.filter((c) => c.path.endsWith("/voice-session"))).toHaveLength(0);
  });
});
