/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { locations, recruiterMe, results } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// The lazy dashboard modules and Recharts' first render are warmed in tests/warm-lazy-routes.tsx.

describe("Results dashboard", () => {
  const api = {
    "GET /api/auth/me": recruiterMe,
    "GET /api/locations": { items: locations },
    "GET /api/metrics/results": results,
  };

  it("shows the funnel, time to qualify, Knockouts, Review overturn rate and the rest", async () => {
    mockApi(api);
    renderRoute("/results");
    expect(await screen.findByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("Time to qualify (median)")).toBeInTheDocument();
    expect(screen.getByText("4m")).toBeInTheDocument();
    expect(screen.getByText("95th percentile 15m")).toBeInTheDocument();
    expect(screen.getByText("28.6%")).toBeInTheDocument();
    expect(screen.getByText("6 Overturned, 15 Upheld, of 21 Reviews")).toBeInTheDocument();
    expect(screen.getByText("Qualified, waiting")).toBeInTheDocument();
    expect(screen.getByText("Takeovers")).toBeInTheDocument();
    expect(screen.getByText("Escalations")).toBeInTheDocument();

    for (const title of ["Applications by Outcome", "Open Applications by stage", "Drop-off by stage", "Knockouts by criterion"]) {
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    }
    // Every chart has its numbers in a table too.
    const outcomes = screen.getByRole("table", { name: "Applications by Outcome (table)", hidden: true });
    expect(within(outcomes).getByText("Knockout").closest("tr")).toHaveTextContent("420");
    const knockouts = screen.getByRole("table", { name: "Knockouts by criterion (table)", hidden: true });
    expect(within(knockouts).getByText("Driver's license").closest("tr")).toHaveTextContent("260");
  });

  it("passes the date, Location and country filters to the API", async () => {
    const mock = mockApi(api);
    renderRoute("/results");
    await screen.findByText("1,000");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("Location"), { target: { value: "loc_mx_puebla_centro" } });
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
    await waitFor(() => {
      const q = mock.find("GET", "/api/metrics/results").at(-1)!.query;
      expect(q.get("from")).toBe("2026-10-01");
      expect(q.get("location_id")).toBe("loc_mx_puebla_centro");
      expect(q.get("country")).toBe("MX");
    });
  });

  it("contains no cost figures", async () => {
    mockApi(api);
    renderRoute("/results");
    await screen.findByText("1,000");
    expect(document.body.textContent).not.toMatch(/\$\d/);
  });

  it("reports a load failure", async () => {
    mockApi({ ...api, "GET /api/metrics/results": error(500, "internal", "boom") });
    renderRoute("/results");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load results. boom");
  });
});

// Phase 4 (recruiter-pages): first load, empty range, error with one Retry request, previous data kept.
// Appended; the four tests above stay as they were.

describe("Results states and layout", () => {
  const api = {
    "GET /api/auth/me": recruiterMe,
    "GET /api/locations": { items: locations },
    "GET /api/metrics/results": results,
  };
  const forever = () => new Promise<object>(() => undefined);
  const skeletons = () => document.querySelectorAll(".ui-skeleton").length;

  it("shows a skeleton of tiles and no Loading text on first load, with the filters in place", async () => {
    mockApi({ ...api, "GET /api/metrics/results": forever });
    renderRoute("/results");
    await screen.findByRole("heading", { level: 1, name: "Results" });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    expect(screen.queryByText("Time to qualify (median)")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("status")).toEqual([]);
  });

  it("says there are no Applications in an empty range, and still shows the range", async () => {
    mockApi({ ...api, "GET /api/metrics/results": { ...results, applications: 0 } });
    renderRoute("/results");
    expect(await screen.findByText(/No Applications in this range/)).toBeInTheDocument();
    expect(screen.queryByText("Time to qualify (median)")).not.toBeInTheDocument();
    expect(document.querySelector(".page-head > span.muted")).toHaveTextContent("2026-10-01 to 2026-10-09");
    expect(skeletons()).toBe(0);
  });

  it("shows an error state whose Retry asks for the results once more", async () => {
    let failing = true;
    const mock = mockApi({ ...api, "GET /api/metrics/results": () => (failing ? error(500, "internal", "boom") : results) });
    renderRoute("/results");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load results");
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    const before = mock.find("GET", "/api/metrics/results").length;
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("1,000")).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/results")).toHaveLength(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the previous results, marked busy, while a changed filter loads", async () => {
    let release: (reply: object) => void = () => undefined;
    const gate = new Promise<object>((resolve) => (release = resolve));
    mockApi({
      ...api,
      "GET /api/metrics/results": (call) => (call.query.get("country") === "MX" ? gate : results),
    });
    renderRoute("/results");
    const total = await screen.findByText("1,000");
    const body = total.closest("[aria-busy]")!;
    expect(body).toHaveAttribute("aria-busy", "false");

    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "MX" } });
    await waitFor(() => expect(body).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(skeletons()).toBe(0);

    release({ ...results, applications: 777 });
    expect(await screen.findByText("777")).toBeInTheDocument();
    expect(body).toHaveAttribute("aria-busy", "false");
  });

  it("keeps the header hooks: div.page-head with the range in a span.muted", async () => {
    mockApi(api);
    renderRoute("/results");
    await screen.findByText("1,000");
    const range = document.querySelector(".page-head > span.muted");
    expect(range).toHaveTextContent("2026-10-01 to 2026-10-09");
    expect(range?.previousElementSibling).toHaveProperty("tagName", "H1");
  });

  it("lists By Location on a table with right-aligned figures and the Location's name", async () => {
    mockApi(api);
    renderRoute("/results");
    const table = await screen.findByRole("table", { name: "By Location" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Location", "Applications", "Qualified", "Knockouts"]);
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Sazón Puebla Centro");
    for (const cell of within(row).getAllByRole("cell").slice(1)) expect(cell).toHaveClass("ui-table__cell--end");
    expect(screen.getByRole("heading", { level: 2, name: "By Location" })).toBeInTheDocument();
  });

  it("shows six tiles in a grid and no cost figure", async () => {
    mockApi(api);
    renderRoute("/results");
    await screen.findByText("1,000");
    expect(document.querySelectorAll(".rp-tiles > *")).toHaveLength(6);
    expect(document.body.textContent).not.toMatch(/\$\d|cost|economics/i);
  });

  it("does not dim the busy results, which would lower their contrast", () => {
    const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/recruiter.css"), "utf8");
    expect(css).not.toMatch(/\.rp-results\[aria-busy[^{]*\{[^}]*opacity/);
  });

  it("lays the tiles out in three columns from 1440px", () => {
    const css = readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/recruiter.css"), "utf8");
    expect(css).toMatch(/@media \(min-width: 90rem\)\s*\{[^@]*\.rp-tiles\s*\{[^}]*repeat\(3,/);
    expect(css).toMatch(/\.rp-page-head \.muted/);
  });
});
