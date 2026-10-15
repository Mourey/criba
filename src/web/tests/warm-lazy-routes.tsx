import { screen } from "@testing-library/react";
import type { RouteObject } from "react-router";
import { beforeAll, vi } from "vitest";
import { routes } from "../src/routes.tsx";
import { comparison, economics, locations, operatorMe, results } from "./unit/fixtures.ts";
import { mockApi, renderRoute } from "./unit/helpers.tsx";

// Vitest setup file (vite.config.ts), so it runs before the tests of every test file.
//
// The dashboards are lazy routes (src/routes.tsx) and Recharts is heavy. The first test that opened
// one used to pay for the module import and for Recharts' first render inside its own body. Cold, and
// under load (coverage in the dev container, parallel workers) that took seconds: findBy's one-second
// window and the five-second test limit both ran out, in whichever file happened to open a dashboard
// first (results.test.tsx and economics.test.tsx, then shell.test.tsx). Pay that cost here, once
// per file, before any test is timed.
//
// A new lazy route is imported by the walk below. Its first render still needs an entry in `pages`,
// which names the route, the API it reads and a text that shows it has drawn.
function lazyRoutes(list: RouteObject[]): RouteObject[] {
  return list.flatMap((route) => [...(route.lazy ? [route] : []), ...lazyRoutes(route.children ?? [])]);
}

// A lazy route is one function, or an object of functions (one per property).
function importers(lazy: RouteObject["lazy"]): Promise<unknown>[] {
  if (typeof lazy === "function") return [lazy()];
  return Object.values(lazy ?? {}).flatMap((load) => (typeof load === "function" ? [load()] : []));
}

const pages: { path: string; api: Parameters<typeof mockApi>[0]; drawn: string }[] = [
  {
    path: "/results",
    api: { "GET /api/auth/me": operatorMe, "GET /api/locations": { items: locations }, "GET /api/metrics/results": results },
    drawn: "Time to qualify (median)",
  },
  {
    path: "/economics",
    api: { "GET /api/auth/me": operatorMe, "GET /api/metrics/economics": economics, "GET /api/metrics/comparison": comparison },
    drawn: "Cost per conversation",
  },
];

beforeAll(async () => {
  await Promise.all(lazyRoutes(routes).flatMap((route) => importers(route.lazy)));
  for (const page of pages) {
    mockApi(page.api);
    const { unmount } = renderRoute(page.path);
    await screen.findByText(page.drawn);
    unmount();
    vi.unstubAllGlobals();
  }
  // Recharts measures tick labels in a hidden span it appends to document.body and leaves there, with
  // the last label ("$0.003") as its text. Drop it so no test reads the warm-up's text from the body;
  // Recharts creates the span again when a chart needs it.
  document.getElementById("recharts_measurement_span")?.remove();
});
