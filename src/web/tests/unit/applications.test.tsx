/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { claimByAna, locations, operatorMe, recruiterMe, summary } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

const rows = [
  summary({
    id: "app_1",
    claim: { ...claimByAna, user_name: "Ana Torres", idle: true },
    flags: ["claimed_idle", "qualified_waiting"],
    stage_durations_seconds: { greeting: 20, name: 15 },
  }),
  summary({
    id: "app_2",
    candidate: { id: "c2", full_name: "María Gómez", phone: "+34612345678", country: "ES", language: "es-ES", opted_out: false },
    stage: "zone",
    flags: ["takeover", "escalation", "stalled", "review_requested"],
    control: "human",
    control_user_name: "Ana Torres",
  }),
];

function setup(items = rows) {
  return mockApi({
    "GET /api/auth/me": operatorMe,
    "GET /api/locations": { items: locations },
    "GET /api/applications": { items, next_cursor: null, total: items.length },
  });
}

describe("Applications view", () => {
  it("lists Applications with Claim holder, time per step and flags", async () => {
    setup();
    renderRoute("/applications");
    const table = await screen.findByRole("table");
    expect(await within(table).findByRole("link", { name: "Luis Hernández" })).toHaveAttribute("href", "/applications/app_1");

    const first = within(table).getAllByRole("row")[1];
    expect(within(first).getByText("Ana Torres")).toBeInTheDocument();
    expect(first).toHaveTextContent("Claimed, idle");
    expect(first).toHaveTextContent("Qualified, waiting");
    expect(within(first).getByRole("img", { name: /Time per step: Greeting 20s, Name 15s/ })).toBeInTheDocument();

    const second = within(table).getAllByRole("row")[2];
    for (const label of ["Takeover", "Escalation", "Stalled", "Review"]) {
      expect(second).toHaveTextContent(label);
    }
    expect(second).toHaveTextContent("Takeover: Ana Torres");
    expect(second).toHaveTextContent("Unclaimed");
    expect(screen.getByText("2 found")).toBeInTheDocument();
  });

  it("sends the filters to the API", async () => {
    const mock = setup();
    renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });

    fireEvent.change(screen.getByLabelText("Location"), { target: { value: "loc_mx_puebla_centro" } });
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
    fireEvent.click(screen.getByLabelText("Zone"));
    fireEvent.click(screen.getByLabelText("Knockout"));
    fireEvent.click(screen.getByLabelText("Claimed, idle"));
    fireEvent.change(screen.getByLabelText("Created from"), { target: { value: "2026-10-01" } });

    await waitFor(() => {
      const last = mock.find("GET", "/api/applications").at(-1)!;
      expect(last.query.get("location_id")).toBe("loc_mx_puebla_centro");
      expect(last.query.get("country")).toBe("MX");
      expect(last.query.getAll("stage")).toEqual(["zone"]);
      expect(last.query.getAll("outcome")).toEqual(["knockout"]);
      expect(last.query.getAll("flag")).toEqual(["claimed_idle"]);
      expect(last.query.get("created_from")).toBe("2026-10-01");
    });
  });

  it("restores filters from the URL and clears them", async () => {
    const mock = setup();
    renderRoute("/applications?stage=zone&stage=name&claimed_by=me");
    await waitFor(() => {
      const last = mock.find("GET", "/api/applications").at(-1)!;
      expect(last.query.getAll("stage")).toEqual(["zone", "name"]);
      expect(last.query.get("claimed_by")).toBe("me");
    });
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(mock.find("GET", "/api/applications").at(-1)!.query.getAll("stage")).toEqual([]));
  });

  describe("search box", () => {
    const SEARCH_DEBOUNCE_MS = 300; // the spec: about 300 ms
    afterEach(() => vi.useRealTimers());

    const queries = (mock: ReturnType<typeof setup>) => mock.find("GET", "/api/applications").map((c) => c.query.get("q"));

    it("waits for a pause in typing before it queries, then sends only the final text", async () => {
      const mock = setup();
      const { router } = renderRoute("/applications");
      await screen.findByRole("link", { name: "Luis Hernández" });
      const before = mock.find("GET", "/api/applications").length;
      const box = screen.getByLabelText("Search Candidate");

      vi.useFakeTimers();
      for (const text of ["M", "Ma", "Mar", "Marí"]) {
        fireEvent.change(box, { target: { value: text } });
        act(() => {
          vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
        });
      }
      // Four keystrokes, each just under the delay apart: nothing queried, nothing in the URL.
      expect(box).toHaveValue("Marí");
      expect(router.state.location.search).toBe("");
      expect(mock.find("GET", "/api/applications")).toHaveLength(before);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(new URLSearchParams(router.state.location.search).get("q")).toBe("Marí");
      vi.useRealTimers();
      await waitFor(() => expect(queries(mock).at(-1)).toBe("Marí"));
      // One request for the whole burst, not one per keystroke.
      expect(mock.find("GET", "/api/applications")).toHaveLength(before + 1);
    });

    it("restores the search from the URL and clears the box with Clear filters", async () => {
      const mock = setup();
      renderRoute("/applications?q=Ana&claimed_by=me");
      const box = await screen.findByLabelText("Search Candidate");
      expect(box).toHaveValue("Ana");
      await waitFor(() => expect(queries(mock).at(-1)).toBe("Ana"));

      fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
      await waitFor(() => expect(box).toHaveValue(""));
      await waitFor(() => expect(queries(mock).at(-1)).toBeNull());
    });

    it("keeps other filters changed while a search is still pending", async () => {
      setup();
      const { router } = renderRoute("/applications");
      await screen.findByRole("link", { name: "Luis Hernández" });
      vi.useFakeTimers();
      fireEvent.change(screen.getByLabelText("Search Candidate"), { target: { value: "Ana" } });
      fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
      act(() => {
        vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      });
      const url = new URLSearchParams(router.state.location.search);
      expect([url.get("q"), url.get("country")]).toEqual(["Ana", "MX"]);
    });
  });

  it("explains an empty result", async () => {
    setup([]);
    renderRoute("/applications");
    expect(await screen.findByText("No Applications match these filters.")).toBeInTheDocument();
  });

  it("loads the next page by cursor", async () => {
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": (call) =>
        call.query.get("cursor")
          ? { items: [rows[1]], next_cursor: null, total: 2 }
          : { items: [rows[0]], next_cursor: "c1", total: 2 },
    });
    renderRoute("/applications");
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("link", { name: "María Gómez" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/applications").at(-1)!.query.get("cursor")).toBe("c1");
  });
});

// Phase 4 (recruiter-pages): filter parity on all 11 parameters, the two-tier layout, and the three states.

const EMPTY_FILTERED = "No Applications match these filters.";
const COLUMNS = ["Candidate", "Locations", "Stage", "Claim holder", "In stage", "Total", "Time per step", "Flags", "Updated"];

describe("filter parity: every parameter has one control that writes and restores it", () => {
  type Case = {
    param: string;
    value: string;
    control: () => HTMLElement;
    show: (control: HTMLElement) => void;
    drive: (control: HTMLElement) => void;
  };
  const select = (label: string) => () => screen.getByLabelText(label);
  const setValue = (control: HTMLElement, value: string) => void fireEvent.change(control, { target: { value } });
  const shows = (value: string) => (control: HTMLElement) => expect(control).toHaveValue(value);
  const cases: Case[] = [
    { param: "q", value: "Marí", control: select("Search Candidate"), drive: (c) => setValue(c, "Marí"), show: shows("Marí") },
    { param: "location_id", value: "loc_mx_puebla_centro", control: select("Location"), drive: (c) => setValue(c, "loc_mx_puebla_centro"), show: shows("loc_mx_puebla_centro") },
    { param: "country", value: "MX", control: select("Country"), drive: (c) => setValue(c, "MX"), show: shows("MX") },
    { param: "status", value: "closed", control: select("Status"), drive: (c) => setValue(c, "closed"), show: shows("closed") },
    { param: "claimed_by", value: "none", control: select("Claim"), drive: (c) => setValue(c, "none"), show: shows("none") },
    { param: "created_from", value: "2026-10-01", control: select("Created from"), drive: (c) => setValue(c, "2026-10-01"), show: shows("2026-10-01") },
    { param: "created_to", value: "2026-10-09", control: select("Created to"), drive: (c) => setValue(c, "2026-10-09"), show: shows("2026-10-09") },
    { param: "sort", value: "-time_in_stage", control: select("Sort"), drive: (c) => setValue(c, "-time_in_stage"), show: shows("-time_in_stage") },
    { param: "stage", value: "zone", control: select("Zone"), drive: (c) => fireEvent.click(c), show: (c) => expect(c).toBeChecked() },
    { param: "outcome", value: "knockout", control: select("Knockout"), drive: (c) => fireEvent.click(c), show: (c) => expect(c).toBeChecked() },
    { param: "flag", value: "claimed_idle", control: select("Claimed, idle"), drive: (c) => fireEvent.click(c), show: (c) => expect(c).toBeChecked() },
  ];

  it.each(cases)("$param: control to API, then URL back to control", async ({ param, value, control, drive, show }) => {
    const mock = setup();
    const first = renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    drive(control());
    await waitFor(() => expect(mock.find("GET", "/api/applications").at(-1)!.query.getAll(param)).toEqual([value]));
    expect(mock.find("GET", "/api/applications").at(-1)!.query.get("limit")).toBe("50");
    first.unmount();

    const again = renderRoute(`/applications?${param}=${encodeURIComponent(value)}`);
    await waitFor(() => show(control()));
    await waitFor(() => expect(mock.find("GET", "/api/applications").at(-1)!.query.getAll(param)).toEqual([value]));
    again.unmount();
  });

  it("defaults the sort to -updated_at and sends it", async () => {
    const mock = setup();
    renderRoute("/applications");
    expect(await screen.findByLabelText("Sort")).toHaveValue("-updated_at");
    await waitFor(() => expect(mock.find("GET", "/api/applications").at(-1)!.query.get("sort")).toBe("-updated_at"));
  });

  it("repeats stage, outcome and flag in the query", async () => {
    const mock = setup();
    renderRoute("/applications?stage=zone&stage=name&outcome=knockout&outcome=rejection&flag=stalled&flag=takeover");
    await waitFor(() => {
      const q = mock.find("GET", "/api/applications").at(-1)!.query;
      expect(q.getAll("stage")).toEqual(["zone", "name"]);
      expect(q.getAll("outcome")).toEqual(["knockout", "rejection"]);
      expect(q.getAll("flag")).toEqual(["stalled", "takeover"]);
    });
  });

  it("Clear filters empties every parameter, the sort included", async () => {
    const mock = setup();
    const { router } = renderRoute("/applications?q=Ana&country=ES&status=open&claimed_by=me&created_from=2026-10-01&created_to=2026-10-09&stage=zone&outcome=knockout&flag=stalled&sort=created_at");
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(router.state.location.search).toBe(""));
    await waitFor(() => expect([...mock.find("GET", "/api/applications").at(-1)!.query.keys()].sort()).toEqual(["limit", "sort"]));
  });
});

describe("one click from Today", () => {
  it("opens Applications from the Main navigation", async () => {
    mockApi({
      "GET /api/auth/me": recruiterMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": { items: rows, next_cursor: null, total: 2 },
    });
    const { router } = renderRoute("/today");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    fireEvent.click(within(nav).getByRole("link", { name: "Applications" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Applications" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/applications");
  });
});

describe("Applications layout", () => {
  const PRIMARY = ["Search Candidate", "Location", "Status", "Claim", "Sort"];
  const HIDDEN = ["Country", "Created from", "Created to", "Zone", "Knockout", "Claimed, idle"];
  const LEGENDS = ["Stage", "Outcome", "Flags"];
  const toggle = () => screen.getByRole("button", { name: /More filters/ });

  it("keeps five controls visible and the other six behind a closed More filters disclosure, every label still found", async () => {
    setup();
    renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    for (const label of PRIMARY) expect(screen.getByLabelText(label), label).toBeVisible();
    for (const label of HIDDEN) {
      expect(screen.getByLabelText(label), `${label} stays mounted`).toBeInTheDocument();
      expect(screen.getByLabelText(label), `${label} is hidden`).not.toBeVisible();
    }
    for (const legend of LEGENDS) expect(screen.getByText(legend, { selector: "legend" }), legend).not.toBeVisible();
    const button = toggle();
    expect(button).toHaveAttribute("aria-expanded", "false");
    const region = document.getElementById(button.getAttribute("aria-controls") ?? "");
    expect(region, "aria-controls names the region").not.toBeNull();
    expect(region).toHaveAttribute("hidden");
    expect(region).toContainElement(screen.getByLabelText("Country"));
    expect(region).not.toContainElement(screen.getByLabelText("Sort"));
    expect(button).toHaveTextContent(/^More filters$/);
  });

  it("opens and closes with the button, showing every control and legend when open", async () => {
    setup();
    renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(toggle().getAttribute("aria-controls") ?? "")).not.toHaveAttribute("hidden");
    for (const label of [...PRIMARY, ...HIDDEN]) expect(screen.getByLabelText(label), label).toBeVisible();
    for (const legend of LEGENDS) expect(screen.getByText(legend, { selector: "legend" }), legend).toBeVisible();
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText("Country")).not.toBeVisible();
  });

  it("opens by itself when the URL holds a hidden filter, and counts the active hidden filters", async () => {
    setup();
    const first = renderRoute("/applications?stage=zone");
    await screen.findByRole("link", { name: "Luis Hernández" });
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Zone")).toBeVisible();
    expect(toggle()).toHaveTextContent("1");
    expect(toggle()).toHaveAccessibleName("More filters 1 active");
    first.unmount();
    // Two stages are one filter; Country, the two dates, Outcome and Flags count one each.
    renderRoute("/applications?stage=zone&stage=name&country=ES&created_from=2026-10-01&created_to=2026-10-09&outcome=knockout&flag=stalled&status=open");
    await screen.findByRole("link", { name: "Luis Hernández" });
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(toggle()).toHaveAccessibleName("More filters 6 active");
  });

  it("stays closed, with no count, when only the always-visible filters are set", async () => {
    setup();
    renderRoute("/applications?q=Ana&location_id=loc_mx_puebla_centro&status=open&claimed_by=me&sort=created_at");
    await screen.findByRole("link", { name: "Luis Hernández" });
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).toHaveAccessibleName("More filters");
    expect(screen.getByLabelText("Country")).not.toBeVisible();
  });

  it("opens when the URL later gains a hidden filter, and keeps a closed state the user chose", async () => {
    setup();
    const { router } = renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    await act(() => router.navigate("/applications?country=MX"));
    await waitFor(() => expect(toggle()).toHaveAttribute("aria-expanded", "true"));
    expect(toggle()).toHaveAccessibleName("More filters 1 active");
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).toHaveAccessibleName("More filters 1 active");
    fireEvent.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(toggle()).toHaveAccessibleName("More filters"));
  });

  it("counts a hidden filter set from inside the open region", async () => {
    setup();
    renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    fireEvent.click(toggle());
    fireEvent.click(screen.getByLabelText("Zone"));
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "ES" } });
    await waitFor(() => expect(toggle()).toHaveAccessibleName("More filters 2 active"));
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
  });

  it("announces the found count in a polite live region and adds no status role", async () => {
    setup();
    renderRoute("/applications");
    const found = await screen.findByText("2 found");
    expect(found).toHaveAttribute("aria-live", "polite");
    expect(found).not.toHaveAttribute("role");
    expect(screen.queryAllByRole("status", { hidden: true })).toEqual([]);
  });

  it("has one h1 and the nine columns in today's order, with the numeric ones right-aligned", async () => {
    setup();
    renderRoute("/applications");
    const table = await screen.findByRole("table");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent)).toEqual(COLUMNS);
    expect(headers[4]).toHaveClass("ui-table__th--end");
    expect(headers[5]).toHaveClass("ui-table__th--end");
    for (const index of [0, 1, 2, 3, 6, 7, 8]) expect(headers[index]).not.toHaveClass("ui-table__th--end");
  });

  it("puts the Candidate link first with the phone under it, and the Locations text in the second cell", async () => {
    setup();
    renderRoute("/applications");
    const table = await screen.findByRole("table");
    const row = within(table).getAllByRole("row")[1];
    const cells = within(row).getAllByRole("cell");
    expect(cells).toHaveLength(9);
    expect(within(cells[0]).getByRole("link", { name: "Luis Hernández" })).toBeInTheDocument();
    expect(cells[0]).toHaveTextContent("+525512345678");
    expect(cells[1]).toHaveTextContent("Sazón Puebla Centro");
    expect(cells[4]).toHaveClass("ui-table__cell--end");
    expect(cells[5]).toHaveClass("ui-table__cell--end");
  });

  it("shows Clear filters for any parameter except sort", async () => {
    setup();
    const first = renderRoute("/applications?sort=created_at");
    await screen.findByRole("link", { name: "Luis Hernández" });
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    first.unmount();
    renderRoute("/applications?sort=created_at&country=ES");
    expect(await screen.findByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("wraps the table in the page's own focusable, named region", async () => {
    setup();
    renderRoute("/applications");
    const table = await screen.findByRole("table");
    const region = screen.getByRole("region", { name: "Applications table" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toContainElement(table);
    expect(region).not.toHaveClass("ui-table__wrap");
  });

  it("points Load more at the found count", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": { items: [rows[0]], next_cursor: "c1", total: 2 },
    });
    renderRoute("/applications");
    const more = await screen.findByRole("button", { name: "Load more" });
    const found = screen.getByText("2 found");
    expect(found.id).not.toBe("");
    expect(more).toHaveAttribute("aria-describedby", found.id);
  });

  it("does not offer a live Load more while a changed filter is loading", async () => {
    let release: (reply: object) => void = () => undefined;
    const gate = new Promise<object>((resolve) => (release = resolve));
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": (call) => (call.query.get("country") === "MX" ? gate : { items: [rows[0]], next_cursor: "c1", total: 2 }),
    });
    renderRoute("/applications");
    const more = await screen.findByRole("button", { name: "Load more" });
    expect(more).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
    await waitFor(() => expect(screen.getByRole("region", { name: "Applications table" })).toHaveAttribute("aria-busy", "true"));
    // Hidden or disabled, never a live button that would page through the previous filter's list.
    const during = screen.queryByRole("button", { name: "Load more" });
    expect(during === null || (during as HTMLButtonElement).disabled).toBe(true);
    release({ items: [rows[1]], next_cursor: null, total: 1 });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument());
  });

  it("styles a sticky header from 1440px and scrolls the region only below it", () => {
    const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/recruiter.css"), "utf8");
    // Table always adds .ui-table__wrap { overflow: auto }; the page's own region is the one scroller.
    expect(css).toMatch(/\.rp-scroll \.ui-table__wrap\s*\{[^}]*overflow:\s*visible/);
    expect(css).toMatch(/\.rp-scroll\s*\{[^}]*overflow-x:\s*auto/);
    expect(css).toMatch(/@media \(min-width: 90rem\)\s*\{[^@]*\.rp-scroll\s*\{[^}]*overflow:\s*(visible|clip)/);
    expect(css).toMatch(/\.rp-scroll \.ui-table\s*\{[^}]*min-width:/);
  });
});

describe("Applications states", () => {
  const forever = () => new Promise<object>(() => undefined);

  it("shows a skeleton and no Loading text on first load", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, "GET /api/locations": { items: locations }, "GET /api/applications": forever });
    renderRoute("/applications");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    await waitFor(() => expect(document.querySelector(".ui-skeleton")).not.toBeNull());
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(EMPTY_FILTERED)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Applications table" })).toHaveAttribute("aria-busy", "true");
    // The shapes are decorative: the page adds no status role beside the preview banner's.
    expect(screen.queryAllByRole("status")).toEqual([]);
  });

  it("says what will appear when there are no Applications at all", async () => {
    setup([]);
    renderRoute("/applications");
    expect(await screen.findAllByText(EMPTY_FILTERED)).toHaveLength(1);
    expect(screen.getByText(/Applications appear here once a Candidate starts Screening/)).toBeInTheDocument();
    expect(screen.queryByText(/Clear a filter/)).not.toBeInTheDocument();
  });

  it("says a filter hides everything when the filtered list is empty", async () => {
    setup([]);
    renderRoute("/applications?country=ES");
    expect(await screen.findAllByText(EMPTY_FILTERED)).toHaveLength(1);
    expect(screen.getByText(/Clear a filter to see more/)).toBeInTheDocument();
    expect(screen.queryByText(/appear here once a Candidate/)).not.toBeInTheDocument();
  });

  it("shows an error state whose Retry asks for the list once more, and keeps the controls", async () => {
    let failing = true;
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": () => (failing ? error(500, "internal", "boom") : { items: rows, next_cursor: null, total: 2 }),
    });
    renderRoute("/applications");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load Applications. boom");
    expect(screen.getByLabelText("Search Candidate")).toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    const before = mock.find("GET", "/api/applications").length;

    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "Luis Hernández" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/applications")).toHaveLength(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers Clear filters beside Retry when a parameter is set, and clearing asks again without it", async () => {
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": (call) => (call.query.get("sort") === "oldest" ? error(422, "invalid", "Unknown sort.") : { items: rows, next_cursor: null, total: 2 }),
    });
    const { router } = renderRoute("/applications?sort=oldest&country=ES");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load Applications. Unknown sort.");
    const clear = screen.getAllByRole("button", { name: "Clear filters" });
    expect(clear).toHaveLength(1);
    fireEvent.click(clear[0]);
    await waitFor(() => expect(router.state.location.search).toBe(""));
    expect(await screen.findByRole("link", { name: "Luis Hernández" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/applications").at(-1)!.query.get("country")).toBeNull();
  });

  it("offers no Clear filters in the error state when nothing is set", async () => {
    mockApi({ "GET /api/auth/me": operatorMe, "GET /api/locations": { items: locations }, "GET /api/applications": error(500, "internal", "boom") });
    renderRoute("/applications");
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("keeps the rows and shows no skeleton while the list refetches", async () => {
    let calls = 0;
    const mock = mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": () => (++calls === 1 ? { items: rows, next_cursor: null, total: 2 } : forever()),
    });
    const { client } = renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    void client.invalidateQueries({ queryKey: ["applications"] });
    await waitFor(() => expect(mock.find("GET", "/api/applications")).toHaveLength(2));
    expect(screen.getByRole("link", { name: "Luis Hernández" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "María Gómez" })).toBeInTheDocument();
    expect(document.querySelector(".ui-skeleton")).toBeNull();
    expect(screen.getByText("2 found")).toBeInTheDocument();
  });

  it("keeps the previous rows, marked busy, while a changed filter loads", async () => {
    let release: (reply: object) => void = () => undefined;
    const gate = new Promise<object>((resolve) => (release = resolve));
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/locations": { items: locations },
      "GET /api/applications": (call) => (call.query.get("country") === "MX" ? gate : { items: rows, next_cursor: null, total: 2 }),
    });
    renderRoute("/applications");
    await screen.findByRole("link", { name: "Luis Hernández" });
    const region = screen.getByRole("region", { name: "Applications table" });
    expect(region).not.toHaveAttribute("aria-busy", "true");

    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
    await waitFor(() => expect(region).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("link", { name: "Luis Hernández" })).toBeInTheDocument();
    expect(document.querySelector(".ui-skeleton")).toBeNull();

    release({ items: [rows[1]], next_cursor: null, total: 1 });
    await waitFor(() => expect(screen.queryByRole("link", { name: "Luis Hernández" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "María Gómez" })).toBeInTheDocument();
    expect(region).not.toHaveAttribute("aria-busy", "true");
  });
});
