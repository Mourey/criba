/// <reference types="node" />
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApplicationSummary, QueueItem, Slot } from "../../src/api/types.ts";
import { formatDateTime } from "../../src/lib/format.ts";
import { locations, operatorMe, puebla, pueblaAngelopolis, queueItem, recruiterMe, summary } from "./fixtures.ts";
import { error, mockApi, renderRoute, type Call } from "./helpers.tsx";

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

// The browser's clock, and the day the default Slot sits on (10:00 in Puebla).
const NOW = "2026-10-10T18:00:00Z";

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.setSystemTime(new Date(NOW));
});

type Handler = Parameters<typeof mockApi>[0][string];
const run = (handler: Handler, call: Call) => (typeof handler === "function" ? handler(call) : handler);

function person(id: string, name: string, over: Partial<ApplicationSummary> = {}): ApplicationSummary {
  const base = summary();
  return summary({ id, candidate: { ...base.candidate, id: `cand_${id}`, full_name: name }, ...over });
}

const ana = person("app_ana", "Ana Ruiz", { flags: ["review_requested"] });
const bruno = person("app_bruno", "Bruno Díaz", { locations: [puebla, pueblaAngelopolis] });
const carla = person("app_carla", "Carla Soto", {
  flags: ["takeover"],
  control: "human",
  control_user_id: "usr_rec_puebla",
  control_user_name: "Recruiter Puebla (synthetic)",
  updated_at: "2026-10-10T09:30:00Z",
});
const dario = person("app_dario", "Darío Vega", {
  flags: ["takeover"],
  control: "human",
  control_user_id: "usr_other",
  control_user_name: "Marta Gil",
  updated_at: "2026-10-10T08:15:00Z",
});
const eva = person("app_eva", "Eva Luna", { updated_at: "2026-10-10T11:00:00Z", flags: ["qualified_waiting"] });
const fran = person("app_fran", "Fran Mora", { updated_at: "2026-10-10T10:00:00Z" });

const approvals: QueueItem[] = [
  queueItem({ id: "qi_ana", kind: "approval", application: ana, created_at: "2026-10-10T08:00:00Z" }),
  queueItem({ id: "qi_bruno", kind: "approval", application: bruno, created_at: "2026-10-10T09:00:00Z" }),
];

function slotAt(id: string, startsAt: string, name: string | null, over: Partial<Slot> = {}): Slot {
  const end = new Date(new Date(startsAt).getTime() + 30 * 60000).toISOString().replace(".000Z", "Z");
  return {
    id,
    location_id: puebla.id,
    location_name: puebla.name,
    starts_at: startsAt,
    ends_at: end,
    local_label: `label ${id}`,
    status: "booked",
    booking: { application_id: `app_${id}`, candidate_name: name, booked_via: "chat", booked_at: "2026-10-09T12:00:00Z" },
    ...over,
  };
}

const gina = slotAt("gina", "2026-10-10T16:00:00Z", "Gina Paz");

const list = (items: ApplicationSummary[]) => ({ items, next_cursor: null, total: items.length });
const queueList = (items: QueueItem[], next: string | null = null) => ({ items, next_cursor: next });

interface Over {
  me?: Handler;
  queue?: Handler;
  takeovers?: Handler;
  qualified?: Handler;
  slots?: Handler;
  locations?: Handler;
  clock?: Handler;
  extra?: Record<string, Handler>;
}

/** The whole Today API, with defaults for every request and an override per section. */
function server(over: Over = {}) {
  return mockApi({
    "GET /api/auth/me": over.me ?? recruiterMe,
    "GET /api/queue": (call) => run(over.queue ?? queueList(approvals), call),
    "GET /api/applications": (call) =>
      call.query.has("flag") ? run(over.takeovers ?? list([carla, dario]), call) : run(over.qualified ?? list([eva, fran]), call),
    "GET /api/slots": (call) => run(over.slots ?? { items: [gina] }, call),
    "GET /api/locations": (call) => run(over.locations ?? { items: locations }, call),
    "GET /api/sim/clock": (call) => run(over.clock ?? error(404, "not_found", "No clock here."), call),
    ...over.extra,
  });
}

/** One section's handler, by its key. */
const only = (key: keyof Over, handler: Handler): Over => ({ [key]: handler }) as Over;

const region = (name: string) => screen.getByRole("region", { name });
const rowOf = (link: string) => screen.getByRole("link", { name: link }).closest("tr") as HTMLElement;
const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 40))));
const isQueue = (c: Call) => c.method === "GET" && c.path === "/api/queue";
const isTakeovers = (c: Call) => c.method === "GET" && c.path === "/api/applications" && c.query.has("flag");
const isQualified = (c: Call) => c.method === "GET" && c.path === "/api/applications" && !c.query.has("flag");
const isSlots = (c: Call) => c.method === "GET" && c.path === "/api/slots";
const isLocations = (c: Call) => c.method === "GET" && c.path === "/api/locations";
const noAuth = (calls: Call[]) => calls.filter((c) => c.path !== "/api/auth/me");
const signature = (calls: Call[]) => noAuth(calls).map((c) => `${c.method} ${c.path}`).sort();

function gate() {
  let open!: () => void;
  const wait = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { wait, open };
}

/** Wait until every section of the default data is on screen. */
async function allSections() {
  await screen.findByRole("link", { name: "Ana Ruiz" });
  await screen.findByRole("link", { name: "Carla Soto" });
  await screen.findByRole("link", { name: "Eva Luna" });
  await screen.findByRole("link", { name: "Gina Paz" });
}

const EXPECTED_CALLS = [
  "GET /api/applications",
  "GET /api/applications",
  "GET /api/locations",
  "GET /api/queue",
  "GET /api/sim/clock",
  "GET /api/slots",
];

describe("Today: the page and its requests (RT1)", () => {
  it.each([
    ["a Recruiter", recruiterMe],
    ["an Operator", operatorMe],
  ] as const)("makes exactly the listed requests for %s", async (_who, me) => {
    const mock = server({ me });
    renderRoute("/today");
    await allSections();
    await settle();
    expect(signature(mock.calls)).toEqual(EXPECTED_CALLS);
    expect(FakeEventSource.instances.map((s) => s.url)).toEqual(["/api/queue/stream"]);
  });

  it("makes the same requests for an Operator who previews the Recruiter view", async () => {
    const mock = server({ me: operatorMe });
    renderRoute("/today");
    await allSections();
    fireEvent.click(await screen.findByRole("button", { name: "Preview as Recruiter" }));
    await screen.findByText(/Previewing the Recruiter view/);
    await settle();
    expect(signature(mock.calls)).toEqual(EXPECTED_CALLS);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("has an h1 and the four regions, in this order, each named by an h2", async () => {
    server();
    renderRoute("/today");
    expect(await screen.findByRole("heading", { level: 1, name: "Today" })).toBeInTheDocument();
    await allSections();
    const names = ["Approvals waiting", "Takeovers waiting on me", "Interviews today", "Recently Qualified"];
    const regions = names.map(region);
    for (let i = 1; i < regions.length; i++) {
      expect(regions[i - 1].compareDocumentPosition(regions[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    for (const name of names) expect(within(region(name)).getByRole("heading", { level: 2, name })).toBeInTheDocument();
  });

  it("leaves the other three sections rendered when the Approvals request fails", async () => {
    server({ queue: error(500, "internal", "The queue is down.") });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Carla Soto" });
    expect(await within(region("Approvals waiting")).findByRole("alert")).toHaveTextContent("The queue is down.");
    expect(within(region("Takeovers waiting on me")).getByRole("link", { name: "Carla Soto" })).toBeInTheDocument();
    expect(await within(region("Interviews today")).findByRole("link", { name: "Gina Paz" })).toBeInTheDocument();
    expect(within(region("Recently Qualified")).getByRole("link", { name: "Eva Luna" })).toBeInTheDocument();
  });
});

describe("Today: Approvals waiting (RT2)", () => {
  it("asks for the open Approvals, 25 at most, and keeps the API order", async () => {
    const mock = server();
    renderRoute("/today");
    await allSections();
    const call = mock.calls.find(isQueue)!;
    expect(call.query.getAll("kind")).toEqual(["approval"]);
    expect(call.query.get("status")).toBe("open");
    expect(call.query.get("limit")).toBe("25");

    const rows = within(region("Approvals waiting")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("link").textContent)).toEqual(["Ana Ruiz", "Bruno Díaz"]);
  });

  it("shows the name as a link, the Locations, the Stage, the flags, the waiting time and Approve", async () => {
    server();
    renderRoute("/today");
    await allSections();
    const row = rowOf("Bruno Díaz");
    expect(within(row).getByRole("link", { name: "Bruno Díaz" })).toHaveAttribute("href", "/applications/app_bruno");
    expect(within(row).getByText("Sazón Puebla Centro, Sazón Puebla Angelópolis")).toBeInTheDocument();
    expect(within(row).getByText("Qualified")).toBeInTheDocument();
    expect(within(row).getByText(formatDateTime("2026-10-10T09:00:00Z"))).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(within(rowOf("Ana Ruiz")).getByText("Review")).toBeInTheDocument();
    expect(within(region("Approvals waiting")).getByRole("columnheader", { name: "Waiting since" })).toBeInTheDocument();
  });

  it("always links to the Review queue, and says more are waiting only when there is a next page", async () => {
    server({ queue: queueList(approvals, "cursor_2") });
    const first = renderRoute("/today");
    await allSections();
    let approvalsRegion = region("Approvals waiting");
    expect(within(approvalsRegion).getByRole("link", { name: "Review queue" })).toHaveAttribute("href", "/queue");
    expect(within(approvalsRegion).getByText(/more in the Review queue/i)).toBeInTheDocument();
    first.unmount();

    server({ queue: queueList(approvals, null) });
    renderRoute("/today");
    await allSections();
    approvalsRegion = region("Approvals waiting");
    expect(within(approvalsRegion).getByRole("link", { name: "Review queue" })).toHaveAttribute("href", "/queue");
    expect(within(approvalsRegion).queryByText(/more in the Review queue/i)).not.toBeInTheDocument();
  });

  it("shows the number of Approvals beside the heading, not inside its name", async () => {
    server();
    renderRoute("/today");
    await allSections();
    const heading = within(region("Approvals waiting")).getByRole("heading", { level: 2 });
    expect(heading).toHaveAccessibleName("Approvals waiting");
    expect(heading.parentElement).toHaveTextContent("2");
  });
});

describe("Today: Takeovers waiting on me (RT3)", () => {
  it("asks for the open Applications in Takeover that I hold a Claim on", async () => {
    const mock = server();
    renderRoute("/today");
    await allSections();
    const call = mock.calls.find(isTakeovers)!;
    expect(call.query.get("status")).toBe("open");
    expect(call.query.getAll("flag")).toEqual(["takeover"]);
    expect(call.query.get("claimed_by")).toBe("me");
    expect(call.query.get("limit")).toBe("25");
  });

  it("shows You for my own Takeover and the holder's name for another user's", async () => {
    server();
    renderRoute("/today");
    await allSections();
    const mine = rowOf("Carla Soto");
    expect(within(mine).getByRole("link", { name: "Carla Soto" })).toHaveAttribute("href", "/applications/app_carla");
    expect(within(mine).getByText("You")).toBeInTheDocument();
    expect(within(mine).getByText(formatDateTime("2026-10-10T09:30:00Z"))).toBeInTheDocument();
    expect(within(mine).queryByRole("button")).not.toBeInTheDocument();

    const theirs = rowOf("Darío Vega");
    expect(within(theirs).getByRole("link", { name: "Darío Vega" })).toHaveAttribute("href", "/applications/app_dario");
    expect(within(theirs).getByText("Marta Gil")).toBeInTheDocument();
    expect(within(theirs).queryByText("You")).not.toBeInTheDocument();
    expect(within(theirs).getByText("Takeover")).toBeInTheDocument();
  });

  it("marks only the Operator's own Takeover as You, by the signed-in user's id", async () => {
    const mine = person("app_op", "Olga Pérez", { control_user_id: "usr_operator", control_user_name: "Operator (synthetic)" });
    server({ me: operatorMe, takeovers: list([mine, dario]) });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Olga Pérez" });
    expect(within(rowOf("Olga Pérez")).getByText("You")).toBeInTheDocument();
    expect(within(rowOf("Darío Vega")).queryByText("You")).not.toBeInTheDocument();
  });
});

describe("Today: Recently Qualified (RT6)", () => {
  it("asks for the open Qualified Applications, newest first, 10 at most", async () => {
    const mock = server();
    renderRoute("/today");
    await allSections();
    const call = mock.calls.find(isQualified)!;
    expect(call.query.get("status")).toBe("open");
    expect(call.query.getAll("stage")).toEqual(["qualified"]);
    expect(call.query.get("sort")).toBe("-updated_at");
    expect(call.query.get("limit")).toBe("10");
    expect(call.query.has("flag")).toBe(false);
  });

  it("keeps the API order and shows the link, Locations, flags and update time", async () => {
    server({ qualified: list([fran, eva]) });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Eva Luna" });
    const rows = within(region("Recently Qualified")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("link").textContent)).toEqual(["Fran Mora", "Eva Luna"]);
    const row = rowOf("Eva Luna");
    expect(within(row).getByRole("link", { name: "Eva Luna" })).toHaveAttribute("href", "/applications/app_eva");
    expect(within(row).getByText("Sazón Puebla Centro")).toBeInTheDocument();
    expect(within(row).getByText("Qualified, waiting")).toBeInTheDocument();
    expect(within(row).getByText(formatDateTime("2026-10-10T11:00:00Z"))).toBeInTheDocument();
  });

  it("links to the Applications list for Qualified, without replacing it", async () => {
    server();
    renderRoute("/today");
    await allSections();
    expect(within(region("Recently Qualified")).getByRole("link", { name: "All Applications" })).toHaveAttribute(
      "href",
      "/applications?stage=qualified",
    );
  });
});

describe("Today: Interviews today (RT4)", () => {
  // 2026-10-09T20:30Z: 14:30 on 9 Oct in Mexico City (UTC-6), 22:30 on 9 Oct in Madrid (UTC+2).
  const NIGHT = "2026-10-09T20:30:00Z";
  const madrid = locations[1];

  it("asks for a three-day window of booked Slots around the hour of now", async () => {
    vi.setSystemTime(new Date(NIGHT));
    const mock = server();
    renderRoute("/today");
    await screen.findByRole("link", { name: "Ana Ruiz" });
    await waitFor(() => expect(mock.calls.filter(isSlots)).toHaveLength(1));
    const call = mock.calls.find(isSlots)!;
    expect(call.query.get("status")).toBe("booked");
    expect(call.query.get("from")).toBe("2026-10-08T08:00:00Z");
    expect(call.query.get("to")).toBe("2026-10-11T08:00:00Z");
    await settle();
    expect(mock.calls.filter(isSlots)).toHaveLength(1);
    expect(mock.calls.filter(isLocations)).toHaveLength(1);
  });

  it("filters on the Location's own day: Mexico City yes, Madrid no, and never on the UTC date", async () => {
    vi.setSystemTime(new Date(NIGHT));
    server({
      slots: {
        items: [
          // 21:00 on 9 Oct in Mexico City: today, although the UTC date is 10 Oct.
          slotAt("mx_late", "2026-10-10T03:00:00Z", "Late Mexico"),
          // 23:00 on 8 Oct in Mexico City: yesterday, although the UTC date is 9 Oct.
          slotAt("mx_early", "2026-10-09T05:00:00Z", "Early Mexico"),
          // 00:30 on 10 Oct in Madrid: tomorrow there.
          slotAt("es_late", "2026-10-09T22:30:00Z", "Late Madrid", { location_id: madrid.id, location_name: madrid.name }),
          // 12:00 on 9 Oct in Madrid: today there.
          slotAt("es_noon", "2026-10-09T10:00:00Z", "Noon Madrid", { location_id: madrid.id, location_name: madrid.name }),
        ],
      },
    });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Late Mexico" });
    const interviews = within(region("Interviews today"));
    expect(interviews.getByRole("link", { name: "Noon Madrid" })).toBeInTheDocument();
    expect(interviews.queryByRole("link", { name: "Early Mexico" })).not.toBeInTheDocument();
    expect(interviews.queryByRole("link", { name: "Late Madrid" })).not.toBeInTheDocument();
  });

  it("shows the label, Location and name, links through the booking, and keeps the API order", async () => {
    server({
      slots: {
        items: [
          gina,
          slotAt("hugo", "2026-10-10T17:00:00Z", "Hugo Ríos", { local_label: "sáb 10 · 11:00" }),
          slotAt("anon", "2026-10-10T18:00:00Z", null, { local_label: "sáb 10 · 12:00" }),
        ],
      },
    });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Gina Paz" });
    const interviews = within(region("Interviews today"));
    const rows = interviews.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("label gina")).toBeInTheDocument();
    expect(within(rows[0]).getByText(puebla.name)).toBeInTheDocument();
    expect(within(rows[0]).getByRole("link", { name: "Gina Paz" })).toHaveAttribute("href", "/applications/app_gina");
    expect(within(rows[1]).getByRole("link", { name: "Hugo Ríos" })).toHaveAttribute("href", "/applications/app_hugo");
    expect(within(rows[2]).getByRole("link", { name: "Candidate (name not given)" })).toHaveAttribute("href", "/applications/app_anon");
  });

  it("leaves out a Slot with an unknown Location, a bad timezone or no booking, and keeps the rest", async () => {
    const bad = { ...locations[0], id: "loc_bad_tz", name: "Sazón Nowhere", timezone: "Not/AZone" };
    server({
      locations: { items: [...locations, bad] },
      slots: {
        items: [
          slotAt("unknown", "2026-10-10T16:00:00Z", "Unknown Place", { location_id: "loc_deleted", location_name: "Gone" }),
          slotAt("badtz", "2026-10-10T16:00:00Z", "Bad Zone", { location_id: bad.id, location_name: bad.name }),
          slotAt("nobooking", "2026-10-10T16:00:00Z", "No Booking", { booking: null }),
          gina,
        ],
      },
    });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Gina Paz" });
    const interviews = within(region("Interviews today"));
    expect(interviews.getAllByRole("row").slice(1)).toHaveLength(1);
    for (const name of ["Unknown Place", "Bad Zone", "No Booking"]) {
      expect(interviews.queryByRole("link", { name })).not.toBeInTheDocument();
    }
    expect(within(region("Approvals waiting")).getByRole("link", { name: "Ana Ruiz" })).toBeInTheDocument();
  });
});

describe("Today: the sim clock as now (RT5)", () => {
  it("waits for the clock, then asks once for a window around the sim date and shows only that day's Slot", async () => {
    const release = gate();
    const decemberSlot = slotAt("xmas", "2026-12-24T19:00:00Z", "Noelia Pino");
    const octoberSlot = slotAt("oct", "2026-10-10T16:00:00Z", "Octavio Gil");
    const mock = server({
      clock: async () => {
        await release.wait;
        return { now: "2026-12-24T18:00:00Z", offset_seconds: 6048000, due_jobs_run: 0 };
      },
      slots: { items: [octoberSlot, decemberSlot] },
    });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Ana Ruiz" });
    await waitFor(() => expect(mock.calls.some((c) => c.path === "/api/sim/clock")).toBe(true));
    await settle();
    expect(mock.calls.filter(isSlots)).toHaveLength(0);
    expect(within(region("Interviews today")).getByRole("status")).toBeInTheDocument();

    release.open();
    await screen.findByRole("link", { name: "Noelia Pino" });
    await settle();
    const calls = mock.calls.filter(isSlots);
    expect(calls).toHaveLength(1);
    expect(calls[0].query.get("from")).toBe("2026-12-23T06:00:00Z");
    expect(calls[0].query.get("to")).toBe("2026-12-26T06:00:00Z");
    expect(within(region("Interviews today")).queryByRole("link", { name: "Octavio Gil" })).not.toBeInTheDocument();
    expect(mock.calls.filter((c) => c.path === "/api/sim/clock")).toHaveLength(1);
  });

  it.each([
    ["404", error(404, "not_found", "No clock here.")],
    ["500", error(500, "internal", "The clock is broken.")],
    ["a body with no usable time", { now: "not a date" }],
    ["a body that is not a clock", { offset_seconds: 5 }],
  ] as const)("falls back to the browser clock when the clock answers %s, with no error shown", async (_what, clock) => {
    const mock = server({ clock });
    renderRoute("/today");
    await screen.findByRole("link", { name: "Gina Paz" });
    await settle();
    const calls = mock.calls.filter(isSlots);
    expect(calls).toHaveLength(1);
    // The browser date is 2026-10-10T18:00Z, so the window is 2026-10-09T06:00Z .. 2026-10-12T06:00Z.
    expect(calls[0].query.get("from")).toBe("2026-10-09T06:00:00Z");
    expect(calls[0].query.get("to")).toBe("2026-10-12T06:00:00Z");
    expect(within(region("Interviews today")).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Today: the queue stream (RT7)", () => {
  it("refreshes the Approvals and both Applications lists on a queue frame, and shows a new item", async () => {
    const newcomer = queueItem({ id: "qi_new", kind: "approval", application: person("app_new", "Nina Ortiz") });
    let frames = 0;
    const mock = server({ queue: () => queueList(frames === 0 ? approvals : [...approvals, newcomer]) });
    renderRoute("/today");
    await allSections();
    await settle();
    expect(mock.calls.filter(isQueue)).toHaveLength(1);
    expect(FakeEventSource.instances).toHaveLength(1);

    frames = 1;
    act(() => FakeEventSource.instances[0].emit("queue", { kind: "queue", id: "qi_new" }));
    expect(await screen.findByRole("link", { name: "Nina Ortiz" })).toHaveAttribute("href", "/applications/app_new");
    await settle();
    expect(mock.calls.filter(isQueue)).toHaveLength(2);
    expect(mock.calls.filter(isTakeovers)).toHaveLength(2);
    expect(mock.calls.filter(isQualified)).toHaveLength(2);
    expect(mock.calls.filter(isSlots)).toHaveLength(1);
  });

  it("refreshes on each (re)open of the stream too", async () => {
    const mock = server();
    renderRoute("/today");
    await allSections();
    await settle();
    act(() => FakeEventSource.instances[0].open());
    await waitFor(() => expect(mock.calls.filter(isQueue)).toHaveLength(2));
  });

  it("closes the one stream when the page goes away", async () => {
    server();
    const { unmount } = renderRoute("/today");
    await allSections();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].closed).toBe(false);
    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });
});

describe("Today: states per section (RT8)", () => {
  const sections = [
    {
      name: "Approvals waiting",
      is: isQueue,
      key: "queue" as const,
      data: queueList(approvals),
      empty: queueList([]),
      sentence: "No Approvals waiting. Qualified Candidates that need your Approval will appear here.",
      row: "Ana Ruiz",
    },
    {
      name: "Takeovers waiting on me",
      is: isTakeovers,
      key: "takeovers" as const,
      data: list([carla, dario]),
      empty: list([]),
      sentence: "No Takeovers waiting on you. Applications you have Claimed with a Takeover in progress will appear here.",
      row: "Carla Soto",
    },
    {
      name: "Interviews today",
      is: isSlots,
      key: "slots" as const,
      data: { items: [gina] },
      empty: { items: [] },
      sentence: "No Interviews today. Booked Interviews at your Locations will appear here on the day.",
      row: "Gina Paz",
    },
    {
      name: "Recently Qualified",
      is: isQualified,
      key: "qualified" as const,
      data: list([eva, fran]),
      empty: list([]),
      sentence: "No Qualified Applications yet. Candidates who finish screening will appear here.",
      row: "Eva Luna",
    },
  ];

  it.each(sections)("$name: a Skeleton in this section only while it loads", async ({ name, key, data, row }) => {
    const hold = gate();
    server(
      only(key, async () => {
        await hold.wait;
        return data;
      }),
    );
    renderRoute("/today");
    await within(await screen.findByRole("region", { name })).findByRole("status", { name: "Loading" });
    for (const other of sections.filter((s) => s.name !== name)) {
      await within(await screen.findByRole("region", { name: other.name })).findByRole("link", { name: other.row });
      expect(within(region(other.name)).queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
    }
    expect(screen.queryByText(/loading\.\.\./i)).not.toBeInTheDocument();
    hold.open();
    await within(region(name)).findByRole("link", { name: row });
    expect(within(region(name)).queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
  });

  it.each(sections)("$name: says what will appear here when empty", async ({ name, key, empty, sentence }) => {
    server(only(key, empty));
    renderRoute("/today");
    expect(await within(await screen.findByRole("region", { name })).findByText(sentence)).toBeInTheDocument();
    // Four distinct sentences: no other section shows this one.
    expect(screen.getAllByText(sentence)).toHaveLength(1);
  });

  it.each(sections)("$name: an error shows the message and Retry asks that endpoint once, and nothing else", async ({ name, key, is, data, row }) => {
    let failures = 1;
    const mock = server(only(key, () => (failures-- > 0 ? error(500, "internal", `${name} broke.`) : data)));
    renderRoute("/today");
    const failed = await screen.findByRole("region", { name });
    expect(await within(failed).findByRole("alert")).toHaveTextContent(`${name} broke.`);
    for (const other of sections.filter((s) => s.name !== name)) {
      await within(await screen.findByRole("region", { name: other.name })).findByRole("link", { name: other.row });
    }
    await settle();
    const before = mock.calls.length;
    fireEvent.click(within(failed).getByRole("button", { name: "Retry" }));
    await within(region(name)).findByRole("link", { name: row });
    await settle();
    const added = mock.calls.slice(before);
    expect(added).toHaveLength(1);
    expect(is(added[0])).toBe(true);
  });

  it.each(sections)("$name: a background refetch keeps the rows and shows no Skeleton", async ({ name, key, data, row }) => {
    const hold = gate();
    let calls = 0;
    server(
      only(key, async () => {
        if (calls++ > 0) await hold.wait;
        return data;
      }),
    );
    const { client } = renderRoute("/today");
    await within(await screen.findByRole("region", { name })).findByRole("link", { name: row });
    await settle();
    if (key === "slots") {
      act(() => void client.invalidateQueries({ queryKey: ["slots"] }));
    } else {
      act(() => FakeEventSource.instances[0].emit("queue", { kind: "queue" }));
    }
    await waitFor(() => expect(calls).toBe(2));
    expect(within(region(name)).getByRole("link", { name: row })).toBeInTheDocument();
    expect(within(region(name)).queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
    hold.open();
    await settle();
    expect(within(region(name)).getByRole("link", { name: row })).toBeInTheDocument();
  });
});

describe("Today: Approve on a row (RT9, RT10)", () => {
  const POST = "POST /api/queue/approvals/:id";

  it("names the button Approve and points aria-describedby at the Candidate's link", async () => {
    server({ extra: { [POST]: { ...approvals[0], status: "resolved" } } });
    renderRoute("/today");
    await allSections();
    const buttons = within(region("Approvals waiting")).getAllByRole("button", { name: "Approve" });
    expect(buttons).toHaveLength(2);
    const link = within(rowOf("Bruno Díaz")).getByRole("link", { name: "Bruno Díaz" });
    const button = within(rowOf("Bruno Díaz")).getByRole("button", { name: "Approve" });
    expect(link.id).toBeTruthy();
    expect(button).toHaveAttribute("aria-describedby", link.id);
    expect(button).toHaveAccessibleDescription("Bruno Díaz");
  });

  it("sends one POST with the decision for one click, the same request the Review queue sends", async () => {
    const mock = server({ extra: { [POST]: { ...approvals[0], status: "resolved" } } });
    renderRoute("/today");
    await allSections();
    fireEvent.click(within(rowOf("Ana Ruiz")).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(mock.find("POST", "/api/queue/approvals/qi_ana")).toHaveLength(1));
    expect(mock.find("POST", "/api/queue/approvals/qi_ana")[0].body).toEqual({ decision: "approved" });
    await settle();
    expect(mock.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("disables the button while the request runs and sends nothing for a second click", async () => {
    const hold = gate();
    const mock = server({
      extra: {
        [POST]: async () => {
          await hold.wait;
          return { ...approvals[0], status: "resolved" };
        },
      },
    });
    renderRoute("/today");
    await allSections();
    const button = within(rowOf("Ana Ruiz")).getByRole("button", { name: "Approve" });
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    fireEvent.click(button);
    await settle();
    expect(mock.find("POST", "/api/queue/approvals/qi_ana")).toHaveLength(1);
    // The other row is not affected.
    expect(within(rowOf("Bruno Díaz")).getByRole("button", { name: "Approve" })).toBeEnabled();
    hold.open();
    await settle();
    expect(mock.find("POST", "/api/queue/approvals/qi_ana")).toHaveLength(1);
  });

  it("keeps the row on a 409, shows the server's message as an alert in the row, and refetches the queue", async () => {
    const mock = server({
      extra: { [POST]: error(409, "already_resolved", "Pedro Ruiz already resolved this Approval.") },
    });
    renderRoute("/today");
    await allSections();
    await settle();
    const before = mock.calls.filter(isQueue).length;
    fireEvent.click(within(rowOf("Ana Ruiz")).getByRole("button", { name: "Approve" }));
    const alert = await within(rowOf("Ana Ruiz")).findByRole("alert");
    expect(alert).toHaveTextContent("Pedro Ruiz already resolved this Approval.");
    expect(screen.getByRole("link", { name: "Ana Ruiz" })).toBeInTheDocument();
    await waitFor(() => expect(mock.calls.filter(isQueue).length).toBe(before + 1));
    expect(within(rowOf("Ana Ruiz")).getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(within(rowOf("Bruno Díaz")).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("on success announces Approved <name> politely, moves focus to the section heading and refreshes", async () => {
    let approved = false;
    const mock = server({
      queue: () => queueList(approved ? [approvals[1]] : approvals),
      extra: {
        [POST]: () => {
          approved = true;
          return { ...approvals[0], status: "resolved" };
        },
      },
    });
    renderRoute("/today");
    await allSections();
    await settle();
    const queueBefore = mock.calls.filter(isQueue).length;
    const applicationsBefore = mock.calls.filter((c) => c.path === "/api/applications").length;

    fireEvent.click(within(rowOf("Ana Ruiz")).getByRole("button", { name: "Approve" }));
    const status = await within(region("Approvals waiting")).findByText("Approved Ana Ruiz");
    expect(status.closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    const heading = within(region("Approvals waiting")).getByRole("heading", { level: 2, name: "Approvals waiting" });
    expect(heading).toHaveAttribute("tabindex", "-1");
    expect(heading).toHaveFocus();

    await waitFor(() => expect(screen.queryByRole("link", { name: "Ana Ruiz" })).not.toBeInTheDocument());
    // The status text stays after the row has gone, and the lists were invalidated.
    expect(within(region("Approvals waiting")).getByText("Approved Ana Ruiz")).toBeInTheDocument();
    expect(mock.calls.filter(isQueue).length).toBe(queueBefore + 1);
    expect(mock.calls.filter((c) => c.path === "/api/applications").length).toBe(applicationsBefore + 2);
    expect(mock.calls.filter((c) => c.path === "/api/applications/app_ana")).toHaveLength(0);
  });
});

describe("Today: status emphasis (RT20)", () => {
  const stageOf = (stage: ApplicationSummary["stage"]) =>
    queueList([queueItem({ id: "qi_ana", kind: "approval", application: person("app_ana", "Ana Ruiz", { stage }) })]);

  it("marks the Stage once when it changes on a refetch, never on first load or an unchanged refetch", async () => {
    let current = stageOf("qualified");
    const mock = server({ queue: () => current });
    const { container } = renderRoute("/today");
    await screen.findByRole("link", { name: "Ana Ruiz" });
    await settle();
    expect(container.querySelector("[data-emphasis]")).toBeNull();

    // An unchanged refetch.
    act(() => FakeEventSource.instances[0].emit("queue", { kind: "queue" }));
    await waitFor(() => expect(mock.calls.filter(isQueue)).toHaveLength(2));
    await settle();
    expect(container.querySelector("[data-emphasis]")).toBeNull();

    // A changed Stage.
    current = stageOf("approved");
    act(() => FakeEventSource.instances[0].emit("queue", { kind: "queue" }));
    await waitFor(() => expect(container.querySelector("[data-emphasis]")).not.toBeNull());
    const marked = container.querySelector("[data-emphasis]") as HTMLElement;
    expect(container.querySelectorAll("[data-emphasis]")).toHaveLength(1);
    expect(marked.tagName).toBe("SPAN");
    expect(marked).toHaveClass("today-emphasis");
    expect(marked).toHaveTextContent("Approved");
    expect(rowOf("Ana Ruiz").contains(marked)).toBe(true);

    // The same value again does not mark it a second time, and does not remount the mark.
    act(() => FakeEventSource.instances[0].emit("queue", { kind: "queue" }));
    await waitFor(() => expect(mock.calls.filter(isQueue)).toHaveLength(4));
    await settle();
    expect(container.querySelectorAll("[data-emphasis]")).toHaveLength(1);
    expect(container.querySelector("[data-emphasis]")).toBe(marked);
  });

  describe("the stylesheet", () => {
    const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/today.css"), "utf8");

    it("animates the mark with token durations and a keyframes rule", () => {
      expect(css).toMatch(/@keyframes\s+today-emphasis/);
      expect(css).toMatch(/\.today-emphasis[^{]*\{[^}]*animation:[^;}]*var\(--motion-/);
    });

    it("turns the animation off under prefers-reduced-motion: reduce", () => {
      const start = css.indexOf("@media (prefers-reduced-motion: reduce)");
      expect(start).toBeGreaterThan(-1);
      const block = css.slice(start, css.indexOf("\n}\n", start));
      expect(block).toMatch(/\.today-emphasis[^{]*\{[^}]*animation:\s*none/);
    });

    it("holds no colour literal and no raw px in spacing", () => {
      expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(css).not.toMatch(/\b(?:rgba?|hsla?|oklch)\(/);
      expect(css).not.toMatch(/(?<![A-Za-z0-9_-])(?:padding|margin|gap|inset|top|right|bottom|left)[a-z-]*\s*:[^;{}]*\d+px/);
    });

    it("never reorders sections with CSS order", () => {
      expect(css).not.toMatch(/(^|[\s;{])order\s*:/);
    });
  });
});

describe("Today: balanced columns and compact Locations (step 7)", () => {
  it("keeps the DOM order, with Approvals alone in one column stack and the other three in the second", async () => {
    server();
    renderRoute("/today");
    await allSections();
    const parent = (name: string) => region(name).parentElement as HTMLElement;
    expect(parent("Takeovers waiting on me")).toBe(parent("Interviews today"));
    expect(parent("Interviews today")).toBe(parent("Recently Qualified"));
    expect(parent("Approvals waiting")).not.toBe(parent("Takeovers waiting on me"));
    expect(parent("Approvals waiting").children).toHaveLength(1);
    const order = [...document.querySelectorAll("section[aria-labelledby]")]
      .map((s) => s.querySelector("h2")?.textContent)
      .filter((t) => ["Approvals waiting", "Takeovers waiting on me", "Interviews today", "Recently Qualified"].includes(t ?? ""));
    expect(order).toEqual(["Approvals waiting", "Takeovers waiting on me", "Interviews today", "Recently Qualified"]);
  });

  it("shows the first Location and +N, with the full list for assistive technology and as a title", async () => {
    server();
    renderRoute("/today");
    await allSections();
    const row = rowOf("Bruno Díaz");
    const name = within(row).getByText("Sazón Puebla Centro");
    const more = within(row).getByText("+1");
    expect(name).toHaveClass("today-loc__name");
    expect(more).toHaveClass("today-loc__more");
    expect(name.parentElement).toBe(more.parentElement);
    expect(name.parentElement).toHaveAttribute("aria-hidden", "true");
    const full = within(row).getByText("Sazón Puebla Centro, Sazón Puebla Angelópolis");
    expect(full).toHaveClass("ui-sr-only");
    expect(name.parentElement?.parentElement).toHaveAttribute("title", "Sazón Puebla Centro, Sazón Puebla Angelópolis");
    // One Location needs no +N and no hidden duplicate.
    const single = within(rowOf("Eva Luna"));
    expect(single.getByText("Sazón Puebla Centro")).not.toHaveAttribute("aria-hidden");
    expect(single.queryByText(/\+\d/)).not.toBeInTheDocument();
  });

  describe("the stylesheet", () => {
    const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/today.css"), "utf8");
    const rule = (selector: string) => css.match(new RegExp(`${selector.replace(/[.]/g, "\\.")}\\s*\\{([^}]*)\\}`))?.[1] ?? "";

    it("truncates the first Location with an ellipsis and keeps +N on one line", () => {
      const name = rule(".today-loc__name");
      expect(name).toMatch(/overflow:\s*hidden/);
      expect(name).toMatch(/text-overflow:\s*ellipsis/);
      expect(name).toMatch(/white-space:\s*nowrap/);
      expect(rule(".today-loc__more")).toMatch(/white-space:\s*nowrap/);
    });
  });
});
