/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { afterEach } from "vitest";
import { ApiError, api, buildQuery, request, setUnauthorizedHandler } from "../../src/api/client.ts";
import { formatDuration, formatUsd } from "../../src/lib/format.ts";
import * as labels from "../../src/lib/labels.ts";
import { flagLabel, stageLabel } from "../../src/lib/labels.ts";
import { addMinutes, localToUtcIso, utcToLocalParts } from "../../src/lib/tz.ts";
import { useSimClock } from "../../src/lib/useSimClock.ts";
import { clockState } from "./fixtures.ts";
import { mockApi } from "./helpers.tsx";

// Read from disk: Vitest swaps every CSS import for an empty string.
const src = (file: string) => readFileSync(join(import.meta.dirname, "../../src", file), "utf8");

describe("buildQuery", () => {
  it("repeats array params and skips empty values", () => {
    expect(buildQuery({ stage: ["zone", "name"], q: "", country: undefined, limit: 50, flag: [] })).toBe("?stage=zone&stage=name&limit=50");
    expect(buildQuery({})).toBe("");
  });
});

describe("request", () => {
  it("maps the Error schema to ApiError", async () => {
    mockApi({
      "POST /api/applications/:id/claim": [409, { error: "already_claimed", message: "Ana Torres already holds the Claim." }],
    });
    const failure = await api.claim("app_1").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, code: "already_claimed", message: "Ana Torres already holds the Claim." });
  });

  it("calls the unauthorized handler on 401, except for login and me", async () => {
    mockApi({
      "GET /api/applications": [401, { error: "unauthorized", message: "Log in to continue." }],
      "GET /api/auth/me": [401, { error: "unauthorized", message: "Log in to continue." }],
    });
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await api.listApplications({}).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await api.me().catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(undefined);
  });

  it("returns undefined for 204 and tolerates non-JSON errors", async () => {
    mockApi({
      "DELETE /api/slots/:id": [204, undefined],
      "GET /api/broken": new Response("<html>bad gateway</html>", { status: 502 }),
    });
    await expect(api.cancelSlot("slot_1")).resolves.toBeUndefined();
    await expect(request("GET", "/api/broken")).rejects.toMatchObject({ status: 502, code: "internal" });
  });

  it("sends JSON bodies and the Prefer-free default headers", async () => {
    const mock = mockApi({ "POST /api/auth/login": [200, { user: {}, locations: [] }] });
    await api.login("a@b.c", "pw");
    expect(mock.calls[0].body).toEqual({ email: "a@b.c", password: "pw" });
  });
});

describe("time zones", () => {
  it("converts Location wall-clock time to UTC", () => {
    expect(localToUtcIso("2026-10-14", "10:00", "America/Mexico_City")).toBe("2026-10-14T16:00:00Z");
    expect(localToUtcIso("2026-10-14", "10:00", "Europe/Madrid")).toBe("2026-10-14T08:00:00Z");
    // Spain is on winter time after 25 October 2026.
    expect(localToUtcIso("2026-10-27", "10:00", "Europe/Madrid")).toBe("2026-10-27T09:00:00Z");
  });

  it("round-trips back to local parts", () => {
    expect(utcToLocalParts("2026-10-14T16:00:00Z", "America/Mexico_City")).toEqual({ date: "2026-10-14", time: "10:00" });
    expect(utcToLocalParts("2026-10-14T23:30:00Z", "Europe/Madrid")).toEqual({ date: "2026-10-15", time: "01:30" });
  });

  it("adds minutes to a time", () => {
    expect(addMinutes("10:00", 30)).toBe("10:30");
    expect(addMinutes("23:45", 30)).toBe("00:15");
  });
});

describe("formatting and labels", () => {
  it("formats durations and money", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(240)).toBe("4m");
    expect(formatDuration(3700)).toBe("1h 1m");
    expect(formatDuration(515000)).toBe("5d 23h");
    expect(formatUsd(0.0031)).toBe("$0.0031");
    expect(formatUsd(3.1)).toBe("$3.10");
    expect(formatUsd(null)).toBe("-");
  });

  it("uses CONTEXT.md terms, not the listed synonyms", () => {
    expect(flagLabel.claimed_idle).toBe("Claimed, idle");
    expect(flagLabel.qualified_waiting).toBe("Qualified, waiting");
    expect(flagLabel.takeover).toBe("Takeover");
    expect(stageLabel.qualified).toBe("Qualified");
    const all = [...Object.values(flagLabel), ...Object.values(stageLabel)].join(" ").toLowerCase();
    for (const avoided of ["handoff", "admin", "case record", "disqualif", "appeal"]) {
      expect(all).not.toContain(avoided);
    }
  });
});

describe("sim clock", () => {
  function renderSimClock() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
    return renderHook(() => useSimClock(), { wrapper });
  }

  it("is available with the parsed instant when the clock answers 200", async () => {
    mockApi({ "GET /api/sim/clock": clockState });
    const { result } = renderSimClock();
    expect(result.current).toEqual({ available: false, now: null, isLoading: true });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.available).toBe(true);
    expect(result.current.now).toEqual(new Date("2026-10-09T10:00:00Z"));
  });

  const unavailable: [string, () => void][] = [
    ["404 sim_disabled", () => mockApi({ "GET /api/sim/clock": [404, { error: "sim_disabled", message: "Not found." }] })],
    ["500", () => mockApi({ "GET /api/sim/clock": [500, { error: "internal", message: "boom" }] })],
    ["a rejected fetch", () => vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")))],
    ["a body without now", () => mockApi({ "GET /api/sim/clock": {} })],
    ["an unparseable now", () => mockApi({ "GET /api/sim/clock": { now: "nope" } })],
    ["an empty 200 body", () => mockApi({ "GET /api/sim/clock": new Response("", { status: 200 }) })],
  ];
  it.each(unavailable)("is unavailable for %s", async (_name, arrange) => {
    arrange();
    const { result } = renderSimClock();
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current).toEqual({ available: false, now: null, isLoading: false });
  });

  describe("between fetches", () => {
    afterEach(() => vi.useRealTimers());

    // The sim clock is real time plus an offset, so `now` has to keep moving after the one fetch.
    // Fake timers freeze the page's Date too, which is what lets the test hold the fetch fixed.
    async function mountAt(realNow: string, serverNow: string) {
      vi.useFakeTimers({ now: new Date(realNow) });
      const mock = mockApi({ "GET /api/sim/clock": { ...clockState, now: serverNow } });
      const view = renderSimClock();
      await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
      return { ...view, mock };
    }

    it("moves `now` on with the page's time while no new fetch happens", async () => {
      const { result, mock } = await mountAt("2026-10-09T10:00:30Z", "2026-10-09T10:00:30Z");
      expect(result.current.now).toEqual(new Date("2026-10-09T10:00:30Z"));
      await act(async () => void (await vi.advanceTimersByTimeAsync(31_000)));
      // Past the minute boundary the hook has ticked: the clock reads 10:01, not the fetched 10:00.
      expect(result.current.now?.toISOString().slice(0, 16)).toBe("2026-10-09T10:01");
      // One minute at a time, as a page lives it: each tick re-renders, and the render sets the next.
      for (let minute = 0; minute < 4; minute++) {
        await act(async () => void (await vi.advanceTimersByTimeAsync(60_000)));
      }
      expect(result.current.now?.toISOString().slice(0, 16)).toBe("2026-10-09T10:05");
      expect(result.current.available).toBe(true);
      expect(mock.find("GET", "/api/sim/clock")).toHaveLength(1);
    });

    it("keeps the sim clock's offset: a server ahead of the page stays ahead", async () => {
      const { result } = await mountAt("2026-10-09T10:00:00Z", "2026-10-16T10:00:30Z");
      await act(async () => void (await vi.advanceTimersByTimeAsync(31_000)));
      expect(result.current.now?.toISOString().slice(0, 16)).toBe("2026-10-16T10:01");
    });

    it("stops ticking on unmount", async () => {
      const { unmount } = await mountAt("2026-10-09T10:00:30Z", "2026-10-09T10:00:30Z");
      const clear = vi.spyOn(globalThis, "clearTimeout");
      unmount();
      expect(clear).toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(5 * 60_000);
      expect(vi.getTimerCount()).toBe(0);
    });

    it("never ticks while the clock is unavailable", async () => {
      vi.useFakeTimers({ now: new Date("2026-10-09T10:00:30Z") });
      mockApi({ "GET /api/sim/clock": [404, { error: "sim_disabled", message: "Not found." }] });
      let renders = 0;
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
      const { result } = renderHook(
        () => {
          renders += 1;
          return useSimClock();
        },
        { wrapper },
      );
      await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
      const settled = renders;
      await act(async () => void (await vi.advanceTimersByTimeAsync(5 * 60_000)));
      expect(result.current).toEqual({ available: false, now: null, isLoading: false });
      // The one-minute staleTime may refetch it; the hook itself adds no render of its own.
      expect(renders - settled).toBeLessThanOrEqual(1);
    });
  });

  it("does not call the unauthorized handler on a 401", async () => {
    mockApi({ "GET /api/sim/clock": [401, { error: "unauthorized", message: "Log in to continue." }] });
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await api.getSimClock().catch(() => undefined);
    expect(handler).not.toHaveBeenCalled();
    setUnauthorizedHandler(undefined);
  });

  it("advances and resets with the contract's bodies", async () => {
    const mock = mockApi({
      "POST /api/sim/clock/advance": { ...clockState, offset_seconds: 3600, due_jobs_run: 1 },
      "POST /api/sim/clock/reset": clockState,
    });
    await expect(api.advanceSimClock({ seconds: 3600 })).resolves.toMatchObject({ offset_seconds: 3600 });
    await api.advanceSimClock({ to: "2026-10-16T10:00:00Z" });
    await expect(api.resetSimClock()).resolves.toEqual(clockState);
    expect(mock.calls.map((c) => [c.method, c.path, c.body])).toEqual([
      ["POST", "/api/sim/clock/advance", { seconds: 3600 }],
      ["POST", "/api/sim/clock/advance", { to: "2026-10-16T10:00:00Z" }],
      ["POST", "/api/sim/clock/reset", undefined],
    ]);
  });
});

describe("per-surface copy", () => {
  it("exposes the stub heading and sentence for today, operator and stage", () => {
    for (const surface of [labels.today, labels.operator, labels.stage]) {
      expect(surface.heading).toMatch(/\S/);
      expect(surface.stubSentence).toMatch(/ will appear here/);
    }
    expect(labels.today.heading).toBe("Today");
    expect(labels.operator.heading).toBe("Overview");
    expect(labels.stage.heading).toBe("Demo stage");
    expect(labels.CLIENT_NAME).toBe("Grupo Sazón");
  });

  it("keeps cost out of Today and Overview and uses CONTEXT.md terms", () => {
    const copy = (surface: Record<string, unknown>) =>
      Object.values(surface).filter((v): v is string => typeof v === "string").join(" ").toLowerCase();
    for (const surface of [labels.today, labels.operator]) {
      expect(copy(surface)).not.toMatch(/cost|economics/);
    }
    const all = [labels.today, labels.operator, labels.stage, labels.recruiter, labels.booking].map(copy).join(" ");
    for (const avoided of ["handoff", "admin", "case record", "disqualif", "appeal", "applicant"]) {
      expect(all).not.toContain(avoided);
    }
  });

  it("re-exports each surface as a namespace that shadows no existing name", () => {
    const text = src("lib/labels.ts");
    const surfaces = ["today", "recruiter", "operator", "stage", "booking"];
    for (const name of surfaces) {
      expect(text).toContain(`export * as ${name} from "./labels/${name}.ts";`);
    }
    const declared = [...text.matchAll(/^export (?:declare )?(?:const|let|var|function|class|type|interface|enum) (\w+)/gm)].map((m) => m[1]);
    const listed = [...text.matchAll(/^export (?:type )?\{([^}]*)\}/gm)].flatMap((m) =>
      m[1].split(",").map((part) => part.trim().split(/\s+as\s+/).pop() ?? "").filter(Boolean),
    );
    expect(declared.length).toBeGreaterThan(0);
    for (const name of surfaces) {
      expect([...declared, ...listed]).not.toContain(name);
    }
  });
});

describe("chart theme", () => {
  it("references only custom properties that tokens.css defines", () => {
    const used = [...src("charts/theme.ts").matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]);
    const tokens = src("styles/tokens.css");
    expect(used.length).toBeGreaterThan(0);
    for (const name of new Set(used)) {
      expect(tokens, name).toMatch(new RegExp(`^\\s*${name}:`, "m"));
    }
  });
});
