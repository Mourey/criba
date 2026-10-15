import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";
import { routes } from "../../src/routes.tsx";
import { operatorMe } from "./fixtures.ts";

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

type Reply = object;
type Handler = Reply | ((call: Call) => Reply | Promise<Reply>);

/**
 * Replace global fetch with an in-memory router. Keys are "METHOD /path" and may contain :params.
 * Anything not listed answers 500 and is recorded, so a missing mock shows up in the assertions.
 * Tests never need a Prism server.
 */
export function mockApi(table: Record<string, Handler>) {
  const calls: Call[] = [];
  const entries = Object.entries(table).map(([key, handler]) => {
    const [method, pattern] = key.split(" ");
    const regex = new RegExp(`^${pattern.replace(/:[a-z_]+/g, "[^/]+")}$`);
    return { method, regex, handler };
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: Call = {
      method,
      path: decodeURIComponent(url.pathname),
      query: url.searchParams,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const entry = entries.find((e) => e.method === method && e.regex.test(call.path));
    if (!entry) {
      return new Response(JSON.stringify({ error: "internal", message: `unmocked ${method} ${call.path}` }), { status: 500 });
    }
    const reply = typeof entry.handler === "function" ? await (entry.handler as (c: Call) => Reply)(call) : entry.handler;
    if (reply instanceof Response) return reply;
    if (Array.isArray(reply) && typeof reply[0] === "number") {
      const [status, body] = reply as [number, unknown];
      return new Response(body === undefined ? null : JSON.stringify(body), { status });
    }
    return new Response(JSON.stringify(reply), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path),
  };
}

export function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, client };
}

export const asOperator = { "GET /api/auth/me": operatorMe };
export const error = (status: number, code: string, message: string): [number, unknown] => [status, { error: code, message }];
