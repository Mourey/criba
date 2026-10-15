// The web chat long poll in useConversation (shared by /test-chat and the Demo stage): a send never queues behind
// the poll's own connection, a changed run state is read at once, and a reply whose run is not over yet is followed
// by a short poll.
import { act, render, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, ChatMessagePage } from "../../src/api/types.ts";
import { useConversation, type Conversation } from "../../src/api/useConversation.ts";

const CONV = "wc_0a1b2c3d4e5f60718293a4b5c6d7e8f9";

function msg(partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "direction" | "sender" | "text">): ChatMessage {
  return {
    message_id: null,
    sender_label: null,
    interactive: null,
    reply_to: null,
    links: [],
    attachments: [],
    created_at: "2026-10-12T10:00:00Z",
    ...partial,
  };
}

const hello = msg({ id: 1, direction: "inbound", sender: "candidate", text: "Hola" });
const reply = msg({ id: 2, direction: "outbound", sender: "agent", text: "¿Tienes licencia?" });

function page(partial: Partial<ChatMessagePage>): ChatMessagePage {
  return { cursor: 0, agent_typing: false, run_finished: true, messages: [], ...partial };
}

interface Read {
  wait: string | null;
  agentTyping: string | null;
  runFinished: string | null;
  signal: AbortSignal | null;
  at: number;
}

/**
 * A fetch stub. Each GET takes the next scripted page; with none left it hangs until its signal aborts, as a long
 * poll does. POSTs answer 202 (or `postStatus`) and record whether the GET before them was still open.
 */
function stubServer(script: ChatMessagePage[], postStatus = 202) {
  const reads: Read[] = [];
  const posts: { openReadAborted: boolean | null }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "POST") {
        const open = reads.at(-1)?.signal ?? null;
        posts.push({ openReadAborted: open ? open.aborted : null });
        const body = postStatus === 202 ? { message_id: "m", id: 9, duplicate: false } : { error: "internal", message: "Could not store the message." };
        return new Response(JSON.stringify(body), { status: postStatus });
      }
      const signal = init?.signal ?? null;
      reads.push({
        wait: url.searchParams.get("wait"),
        agentTyping: url.searchParams.get("agent_typing"),
        runFinished: url.searchParams.get("run_finished"),
        signal,
        at: Date.now(),
      });
      const next = script.shift();
      if (next) return new Response(JSON.stringify(next), { status: 200 });
      return new Promise<Response>((_, reject) => {
        const abort = () => reject(new DOMException("aborted", "AbortError"));
        if (signal?.aborted) abort();
        signal?.addEventListener("abort", abort, { once: true });
      });
    }),
  );
  return { reads, posts };
}

function mountHook() {
  const seen: { current: Conversation | null } = { current: null };
  function Harness() {
    const value = useConversation({ storageKey: null });
    useEffect(() => {
      seen.current = value;
    });
    return null;
  }
  render(<Harness />);
  return () => seen.current as Conversation;
}

describe("useConversation long poll", () => {
  it("aborts its own open long poll before a send, then polls again", async () => {
    const server = stubServer([page({ cursor: 1, messages: [hello] })]);
    const now = mountHook();
    act(() => now().resume(CONV));
    await waitFor(() => expect(server.reads.length).toBe(2)); // the first page, then the open long poll
    await act(async () => {
      expect(await now().send("Ana López")).toBe(true);
    });
    // A browser holds six connections per host: the send must not wait behind the poll's own.
    expect(server.posts).toEqual([{ openReadAborted: true }]);
    await waitFor(() => expect(server.reads.length).toBe(3));
    expect(server.reads[2].signal?.aborted).toBe(false);
  });

  it("aborts the open long poll before a tap, and polls again after a failed send", async () => {
    const question = msg({ ...reply, interactive: { style: "buttons", options: [{ id: "yes", label: "Sí" }] } });
    const server = stubServer([page({ cursor: 2, messages: [question] })], 500);
    const now = mountHook();
    act(() => now().resume(CONV));
    await waitFor(() => expect(server.reads.length).toBe(2));
    await act(async () => {
      expect(await now().tap(question, "yes")).toBe(false);
    });
    expect(server.posts).toEqual([{ openReadAborted: true }]);
    await waitFor(() => expect(server.reads.length).toBe(3));
    await act(async () => {
      expect(await now().send("sí")).toBe(false);
    });
    expect(server.posts[1]).toEqual({ openReadAborted: true });
    // The poll comes back after a failure too, or the conversation would stop updating.
    await waitFor(() => expect(server.reads.length).toBe(4));
    expect(server.reads[3].signal?.aborted).toBe(false);
  });

  it("reads again at once when a page brings no message but a changed run state", async () => {
    const server = stubServer([
      page({ cursor: 2, agent_typing: true, run_finished: false, messages: [hello, reply] }),
      // The server returned early: the run ended with nothing new to show.
      page({ cursor: 2, agent_typing: false, run_finished: true }),
    ]);
    const now = mountHook();
    act(() => now().resume(CONV));
    await waitFor(() => expect(now().runFinished).toBe(true));
    expect(now().typing).toBe(false);
    // No 1.5 s back-off after a page that changed the run state.
    await waitFor(() => expect(server.reads.length).toBe(3), { timeout: 1000 });
    expect(server.reads[2].at - server.reads[1].at).toBeLessThan(1000);
  });

  it("sends the run state of its last page with the next poll, and none with the first", async () => {
    // A run that ends between two polls changes nothing the server can see from its own first read; the client says
    // what it saw, so that poll returns at once instead of at its deadline.
    const server = stubServer([
      page({ cursor: 1, agent_typing: true, run_finished: false, messages: [hello] }),
      page({ cursor: 2, agent_typing: false, run_finished: true, messages: [reply] }),
    ]);
    const now = mountHook();
    act(() => now().resume(CONV));
    await waitFor(() => expect(server.reads.length).toBe(3));
    expect(server.reads.map((r) => [r.agentTyping, r.runFinished])).toEqual([
      [null, null],
      ["true", "false"],
      ["false", "true"],
    ]);
  });

  it("holds the poll after a reply whose run is not over for only a couple of seconds", async () => {
    const server = stubServer([
      page({ cursor: 1, agent_typing: true, run_finished: false, messages: [hello] }),
      page({ cursor: 2, agent_typing: true, run_finished: false, messages: [reply] }),
      page({ cursor: 2, agent_typing: false, run_finished: true }),
    ]);
    const now = mountHook();
    act(() => now().resume(CONV));
    await waitFor(() => expect(server.reads.length).toBe(4));
    // The Candidate's own message does not shorten the poll; the agent's reply with the run still open does.
    expect(server.reads.map((r) => r.wait)).toEqual(["20", "20", "2", "20"]);
  });
});
