/// <reference types="node" />
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MotionConfigContext } from "motion/react";
import { useContext, type ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { routes } from "./routes.tsx";

// Setup files (tests/warm-lazy-routes.tsx) load app modules before this file's mock is registered, so
// the motion test re-imports App after vi.resetModules: the result never depends on what they loaded.
// Every MotionProvider mount is recorded with the reducedMotion its children see, and wraps them in
// a marker element so the test can tell what it holds. The real provider still does the work.
const motionMounts = vi.hoisted(() => [] as (string | undefined)[]);
vi.mock("./components/ui/MotionProvider.tsx", async (importOriginal) => {
  const real = await importOriginal<typeof import("./components/ui/MotionProvider.tsx")>();
  function Probe() {
    motionMounts.push(useContext(MotionConfigContext).reducedMotion);
    return null;
  }
  return {
    MotionProvider: ({ children }: { children: ReactNode }) => (
      <real.MotionProvider>
        <Probe />
        <div data-testid="motion-provider">{children}</div>
      </real.MotionProvider>
    ),
  };
});

afterEach(() => vi.unstubAllGlobals());

describe("app shell", () => {
  it("sends a logged-out visitor to the login page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "unauthorized", message: "Log in to continue." }), { status: 401 })),
    );
    const router = createMemoryRouter(routes, { initialEntries: ["/"] });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });
});

describe("motion wiring", () => {
  it("renders the router inside one MotionProvider with reducedMotion user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "unauthorized", message: "Log in to continue." }), { status: 401 })),
    );
    vi.resetModules();
    const { App } = await import("./App.tsx");
    render(<App />);
    const login = await screen.findByRole("button", { name: "Log in" });
    expect(screen.getAllByTestId("motion-provider")).toHaveLength(1);
    expect(screen.getByTestId("motion-provider")).toContainElement(login);
    expect(new Set(motionMounts)).toEqual(new Set(["user"]));
  });

  it("mounts MotionProvider in App.tsx only, and MotionConfig nowhere else", () => {
    const root = import.meta.dirname;
    const files = (readdirSync(root, { recursive: true }) as string[])
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
      .map((f) => relative(root, join(root, f)));
    // components/ui/ is the design system's: MotionProvider itself and its own specimen page.
    const app = files.filter((f) => !f.startsWith("components/ui/"));
    const mounting = app.filter((f) => /<MotionProvider\b|<MotionConfig\b/.test(readFileSync(join(root, f), "utf8")));
    expect(mounting).toEqual(["App.tsx"]);
  });
});
