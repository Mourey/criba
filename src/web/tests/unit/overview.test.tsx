/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { comparison, economics, locations, operatorMe, puebla, recruiterMe, results } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// The Operator's Overview (spec OP1-OP6). The lazy modules and Recharts' first render are warmed in
// tests/warm-lazy-routes.tsx. Preview as Recruiter is a sessionStorage flag (AuthProvider).

const api = {
  "GET /api/auth/me": operatorMe,
  "GET /api/locations": { items: locations },
  "GET /api/metrics/results": results,
  "GET /api/metrics/economics": economics,
  "GET /api/metrics/comparison": comparison,
};

const previewOn = () => window.sessionStorage.setItem("criba.previewRecruiter", "1");
const forever = () => new Promise<object>(() => undefined);
const skeletons = () => document.querySelectorAll(".ui-skeleton").length;
const tile = (label: string) => screen.getByText(label).closest(".stat") as HTMLElement;
const COST_PATHS = ["/api/metrics/economics", "/api/metrics/comparison"];
const costCalls = (mock: ReturnType<typeof mockApi>) => mock.calls.filter((c) => COST_PATHS.includes(c.path));
const loaded = () => screen.findByRole("region", { name: "Pipeline" });

describe("Overview pipeline and Outcomes (OP1)", () => {
  it("sums the open stages into five groups and lists the four Outcomes in each chart's table", async () => {
    mockApi(api);
    renderRoute("/overview");
    await loaded();
    const pipeline = screen.getByRole("table", { name: "Pipeline (table)", hidden: true });
    const rows = within(pipeline)
      .getAllByRole("row", { hidden: true })
      .slice(1)
      .map((r) => [...r.querySelectorAll("td")].map((td) => td.textContent));
    expect(rows).toEqual([
      ["Applied", "12"],
      ["Screening", "27"],
      ["Qualified", "40"],
      ["Approved", "15"],
      ["Interview booked", "22"],
    ]);
    const outcomes = screen.getByRole("table", { name: "Applications by Outcome (table)", hidden: true });
    for (const [label, value] of [
      ["Knockout", "420"],
      ["Rejection", "30"],
      ["Abandoned", "380"],
      ["Withdrawn", "54"],
    ]) {
      expect(within(outcomes).getByText(label).closest("tr")).toHaveTextContent(value);
    }
  });

  it("keeps a table view with every chart", async () => {
    mockApi(api);
    renderRoute("/overview");
    await loaded();
    expect(screen.getAllByText("Table view").length).toBeGreaterThanOrEqual(2);
  });

  it("counts a missing stage key as 0", async () => {
    const rest = Object.fromEntries(Object.entries(results.funnel.open_by_stage).filter(([stage]) => stage !== "greeting" && stage !== "qualified"));
    mockApi({ ...api, "GET /api/metrics/results": { ...results, funnel: { ...results.funnel, open_by_stage: rest } } });
    renderRoute("/overview");
    await loaded();
    const pipeline = screen.getByRole("table", { name: "Pipeline (table)", hidden: true });
    expect(within(pipeline).getByText("Applied").closest("tr")).toHaveTextContent("0");
    expect(within(pipeline).getByText("Qualified").closest("tr")).toHaveTextContent("0");
    expect(within(pipeline).getByText("Screening").closest("tr")).toHaveTextContent("27");
  });

  it("shows no percentage in the pipeline card and the period beside the h1", async () => {
    mockApi(api);
    renderRoute("/overview");
    const pipeline = await loaded();
    expect(pipeline.textContent).not.toContain("%");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText("2026-10-01 to 2026-10-09")).toBeInTheDocument();
  });
});

describe("Overview signals and Locations (OP2)", () => {
  it("shows Escalations, Takeovers and Qualified, waiting", async () => {
    mockApi(api);
    renderRoute("/overview");
    await loaded();
    expect(tile("Escalations")).toHaveTextContent("25");
    expect(tile("Takeovers")).toHaveTextContent("18");
    expect(tile("Qualified, waiting")).toHaveTextContent("3");
  });

  it("lists By Location with the Location name", async () => {
    mockApi(api);
    renderRoute("/overview");
    expect(await screen.findByRole("heading", { name: "By Location" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "By Location" });
    await waitFor(() => expect(within(table).getByText(puebla.name).closest("tr")).toHaveTextContent("300"));
    const row = within(table).getByText(puebla.name).closest("tr")!;
    expect(row).toHaveTextContent("90");
    expect(row).toHaveTextContent("120");
  });

  it("renders no By Location section and no error without by_location", async () => {
    const withoutLocations = { ...results, by_location: undefined };
    const mock = mockApi({ ...api, "GET /api/metrics/results": withoutLocations });
    renderRoute("/overview");
    await loaded();
    expect(screen.queryByRole("heading", { name: "By Location" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mock.find("GET", "/api/locations")).toHaveLength(0);
  });

  it("renders no By Location section for an empty list", async () => {
    mockApi({ ...api, "GET /api/metrics/results": { ...results, by_location: [] } });
    renderRoute("/overview");
    await loaded();
    expect(screen.queryByRole("heading", { name: "By Location" })).not.toBeInTheDocument();
  });

  it("shows the location_id when the Locations request fails", async () => {
    mockApi({ ...api, "GET /api/locations": error(500, "internal", "boom") });
    renderRoute("/overview");
    const table = await screen.findByRole("table", { name: "By Location" });
    await waitFor(() => expect(within(table).getByText(puebla.id)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Overview economics row, Operator only (OP3)", () => {
  it("shows Cost per conversation, Conversations and Latency p50 to an Operator, once each", async () => {
    mockApi(api);
    renderRoute("/overview");
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(screen.getAllByText("Cost per conversation")).toHaveLength(1);
    expect(screen.getAllByText("Latency p50")).toHaveLength(1);
    expect(tile("Cost per conversation")).toHaveTextContent("$0.0031");
    expect(tile("Conversations")).toHaveTextContent("1,000");
    expect(tile("Latency p50")).toHaveTextContent("1.7 s");
    expect(tile("Latency p50")).toHaveTextContent("p95 3.9 s");
  });

  it("shows a dash for a null cost and keeps the tiles when there are no conversations", async () => {
    mockApi({
      ...api,
      "GET /api/metrics/economics": { ...economics, conversations: 0, cost_usd: { ...economics.cost_usd, per_conversation: null } },
    });
    renderRoute("/overview");
    await screen.findByText("Cost per conversation");
    expect(tile("Cost per conversation")).toHaveTextContent("-");
    expect(tile("Conversations")).toHaveTextContent("0");
  });

  it("shows a Recruiter the pipeline only: no cost, no tabs, no cost request", async () => {
    const mock = mockApi({ ...api, "GET /api/auth/me": recruiterMe });
    renderRoute("/overview");
    await loaded();
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(tile("Escalations")).toHaveTextContent("25");
    expect(document.body.textContent).not.toMatch(/\$/);
    expect(screen.queryByText("Cost per conversation")).not.toBeInTheDocument();
    expect(screen.queryByText("Latency p50")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(costCalls(mock)).toHaveLength(0);
  });

  it("shows an Operator under Preview as Recruiter the pipeline only", async () => {
    previewOn();
    const mock = mockApi(api);
    renderRoute("/overview");
    await loaded();
    expect(document.body.textContent).not.toMatch(/\$/);
    expect(screen.queryByText("Cost per conversation")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(costCalls(mock)).toHaveLength(0);
  });

  it("hides every cost element when an Operator who loaded the row previews as Recruiter in the same session", async () => {
    const mock = mockApi(api);
    renderRoute("/overview");
    await screen.findByText("Cost per conversation");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const before = costCalls(mock).length;
    expect(before).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByText("Cost per conversation")).not.toBeInTheDocument());
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText("Latency p50")).not.toBeInTheDocument();
    expect(screen.queryByText("Conversations")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\$/);
    expect(costCalls(mock)).toHaveLength(before);
    // The pipeline is still there.
    expect(screen.getByRole("region", { name: "Pipeline" })).toBeInTheDocument();
  });
});

describe("Overview tabs (OP4)", () => {
  it("shows Economics and Comparison tabs to an Operator, Economics selected by default", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("tablist");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Economics", "Comparison"]);
    expect(screen.getByRole("tab", { name: "Economics" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Comparison" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Economics" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Comparison" })).toHaveAttribute("tabindex", "-1");
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAccessibleName("Economics");
    expect(screen.getByRole("tab", { name: "Economics" })).toHaveAttribute("aria-controls", panel.id);
    expect(await within(panel).findByRole("heading", { name: "Model mix" })).toBeInTheDocument();
  });

  it("selects Comparison from ?tab=comparison and shows only the dataset field", async () => {
    mockApi(api);
    renderRoute("/overview?tab=comparison");
    await screen.findByRole("tablist");
    expect(screen.getByRole("tab", { name: "Comparison" })).toHaveAttribute("aria-selected", "true");
    const panel = screen.getByRole("tabpanel");
    expect(await within(panel).findByRole("heading", { name: "Model comparison" })).toBeInTheDocument();
    expect(await within(panel).findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
    expect(screen.getByLabelText("Comparison dataset")).toBeInTheDocument();
    for (const label of ["From", "To", "Environment", "Source"]) expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Model mix" })).not.toBeInTheDocument();
  });

  it("falls back to Economics for an unknown ?tab=", async () => {
    mockApi(api);
    renderRoute("/overview?tab=bogus");
    await screen.findByRole("tablist");
    expect(screen.getByRole("tab", { name: "Economics" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Model mix" })).toBeInTheDocument();
  });

  it("moves and selects with ArrowRight, ArrowLeft, Home and End, and keeps the choice in the URL", async () => {
    mockApi(api);
    const { router } = renderRoute("/overview");
    await screen.findByRole("tablist");
    const economicsTab = screen.getByRole("tab", { name: "Economics" });
    const comparisonTab = screen.getByRole("tab", { name: "Comparison" });
    economicsTab.focus();

    fireEvent.keyDown(economicsTab, { key: "ArrowRight" });
    expect(comparisonTab).toHaveAttribute("aria-selected", "true");
    expect(comparisonTab).toHaveFocus();
    expect(comparisonTab).toHaveAttribute("tabindex", "0");
    expect(economicsTab).toHaveAttribute("tabindex", "-1");
    expect(router.state.location.search).toBe("?tab=comparison");

    fireEvent.keyDown(comparisonTab, { key: "ArrowLeft" });
    expect(economicsTab).toHaveAttribute("aria-selected", "true");
    expect(economicsTab).toHaveFocus();

    fireEvent.keyDown(economicsTab, { key: "End" });
    expect(comparisonTab).toHaveAttribute("aria-selected", "true");
    expect(comparisonTab).toHaveFocus();

    fireEvent.keyDown(comparisonTab, { key: "Home" });
    expect(economicsTab).toHaveAttribute("aria-selected", "true");
    expect(economicsTab).toHaveFocus();
  });

  it("selects a tab by click", async () => {
    mockApi(api);
    const { router } = renderRoute("/overview");
    await screen.findByRole("tablist");
    fireEvent.click(screen.getByRole("tab", { name: "Comparison" }));
    expect(screen.getByRole("tab", { name: "Comparison" })).toHaveAttribute("aria-selected", "true");
    expect(router.state.location.search).toBe("?tab=comparison");
    expect(await screen.findByRole("heading", { name: "Model comparison" })).toBeInTheDocument();
  });

  it("shows Cost per conversation exactly once on both tabs", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByText("Cost per conversation");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(screen.getAllByText("Cost per conversation")).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", { name: "Comparison" }));
    await screen.findByRole("heading", { name: "Model comparison" });
    expect(screen.getAllByText("Cost per conversation")).toHaveLength(1);
  });

  it("keeps a filter value across a tab switch and sends it", async () => {
    const mock = mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("heading", { name: "Model mix" });
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "sim" } });
    await waitFor(() => expect(mock.find("GET", "/api/metrics/economics").at(-1)!.query.get("environment")).toBe("sim"));
    fireEvent.click(screen.getByRole("tab", { name: "Comparison" }));
    await screen.findByRole("heading", { name: "Model comparison" });
    fireEvent.click(screen.getByRole("tab", { name: "Economics" }));
    expect(await screen.findByLabelText("Environment")).toHaveValue("sim");
  });

  it("makes no comparison request until the Comparison tab is open", async () => {
    const mock = mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(mock.find("GET", "/api/metrics/comparison")).toHaveLength(0);
  });
});

describe("Overview refresh every 15 s (OP5)", () => {
  const changed = {
    ...results,
    escalations: 26,
    by_location: [{ location_id: puebla.id, applications: 301, qualified: 91, knockouts: 121 }],
  };

  it("refetches results and the economics row once each, in place, with the same nodes and no Skeleton", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    const mock = mockApi({ ...api, "GET /api/metrics/results": () => (n++ === 0 ? results : changed) });
    renderRoute("/overview");
    await screen.findByRole("table", { name: "By Location" });
    await screen.findByText("Cost per conversation");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(mock.find("GET", "/api/metrics/results")).toHaveLength(1);
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(1);

    const table = screen.getByRole("table", { name: "By Location" });
    const row = within(table).getByText(puebla.name).closest("tr");
    const surface = document.querySelector(".recharts-surface");
    const pipeline = screen.getByRole("region", { name: "Pipeline" });
    expect(surface).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(tile("Escalations")).toHaveTextContent("26"));
    await waitFor(() => expect(within(table).getByText("301")).toBeInTheDocument());
    expect(mock.find("GET", "/api/metrics/results")).toHaveLength(2);
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(2);

    expect(screen.getByRole("table", { name: "By Location" })).toBe(table);
    expect(within(table).getByText(puebla.name).closest("tr")).toBe(row);
    expect(document.querySelector(".recharts-surface")).toBe(surface);
    expect(screen.getByRole("region", { name: "Pipeline" })).toBe(pipeline);
    expect(skeletons()).toBe(0);
  });

  it("keeps the numbers and says so after a failed refetch, then recovers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    mockApi({
      ...api,
      "GET /api/metrics/results": () => {
        n += 1;
        if (n === 2) return error(500, "internal", "boom");
        return n === 1 ? results : { ...results, escalations: 27 };
      },
    });
    renderRoute("/overview");
    await loaded();
    await screen.findByText("Cost per conversation");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("status")).toHaveLength(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(await screen.findByText(/^Could not refresh\. Showing data from \d{2}:\d{2}\.$/)).toBeInTheDocument();
    expect(tile("Escalations")).toHaveTextContent("25");
    expect(screen.getByRole("region", { name: "Pipeline" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(skeletons()).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(tile("Escalations")).toHaveTextContent("27"));
    expect(screen.queryByText(/Could not refresh/)).not.toBeInTheDocument();
  });

  it("sends no economics request on the interval under Preview as Recruiter", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    previewOn();
    const mock = mockApi(api);
    renderRoute("/overview");
    await loaded();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(mock.find("GET", "/api/metrics/results")).toHaveLength(2));
    expect(costCalls(mock)).toHaveLength(0);
  });

  it("does not refetch the comparison", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const mock = mockApi(api);
    renderRoute("/overview?tab=comparison");
    await screen.findByText("Dataset compare-2026-10-12, 50 personas.");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await waitFor(() => expect(mock.find("GET", "/api/metrics/results").length).toBeGreaterThanOrEqual(2));
    expect(mock.find("GET", "/api/metrics/comparison")).toHaveLength(1);
  });
});

describe("Overview states (OP6)", () => {
  it("shows Skeletons on first load and the h1", async () => {
    mockApi({ ...api, "GET /api/metrics/results": forever, "GET /api/metrics/economics": forever });
    renderRoute("/overview");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/Loading\.\.\.|Loading…/);
    expect(screen.queryByRole("region", { name: "Pipeline" })).not.toBeInTheDocument();
  });

  it("says what will appear when there are no Applications, and still shows the three tiles", async () => {
    mockApi({ ...api, "GET /api/metrics/results": { ...results, applications: 0, by_location: [] } });
    renderRoute("/overview");
    expect(
      await screen.findByText("No Applications in this period. The pipeline fills as Candidates start chatting."),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Pipeline" })).not.toBeInTheDocument();
    expect(tile("Escalations")).toHaveTextContent("25");
    expect(tile("Takeovers")).toHaveTextContent("18");
    expect(tile("Qualified, waiting")).toHaveTextContent("3");
  });

  it("shows an error with Retry that sends exactly one more request", async () => {
    let n = 0;
    const mock = mockApi({
      ...api,
      "GET /api/metrics/results": () => (n++ === 0 ? error(500, "internal", "boom") : results),
    });
    renderRoute("/overview");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load the Overview. boom");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/results")).toHaveLength(1);
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await loaded();
    expect(mock.find("GET", "/api/metrics/results")).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("leaves the pipeline on screen when the economics request fails", async () => {
    mockApi({ ...api, "GET /api/metrics/economics": error(500, "internal", "ledger down") });
    renderRoute("/overview");
    await loaded();
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("ledger down");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Pipeline" })).toBeInTheDocument();
    expect(tile("Escalations")).toHaveTextContent("25");
  });

  it("shows one alert and one Retry when the economics fail, and Retry sends exactly one more request", async () => {
    let n = 0;
    const mock = mockApi({
      ...api,
      "GET /api/metrics/economics": () => (n++ === 0 ? error(500, "internal", "ledger down") : economics),
    });
    renderRoute("/overview");
    await loaded();
    await screen.findByRole("alert");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Model mix" })).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("keeps one h1 and descends one level at a time", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const levels = screen.getAllByRole("heading").map((h) => Number(h.tagName.slice(1)));
    for (let i = 1; i < levels.length; i += 1) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
  });
});

// Design critique round: grouped figures with links, all-zero charts, filter hints, the 768px tile columns.

const operatorCss = () => readFileSync(join(import.meta.dirname, "../../src/styles/surfaces/operator.css"), "utf8");

describe("Overview grouped figures (critique 1)", () => {
  it("puts the signals in one Signals card, with real links that name the figure", async () => {
    mockApi(api);
    renderRoute("/overview");
    await loaded();
    const signals = screen.getByRole("region", { name: "Signals" });
    expect(within(signals).getByRole("heading", { level: 2, name: "Signals" })).toBeInTheDocument();
    const escalations = within(signals).getByRole("link", { name: /Escalations/ });
    const takeovers = within(signals).getByRole("link", { name: /Takeovers/ });
    const waiting = within(signals).getByRole("link", { name: /Qualified, waiting/ });
    expect(escalations).toHaveAttribute("href", "/queue");
    expect(takeovers).toHaveAttribute("href", "/queue");
    expect(waiting).toHaveAttribute("href", "/applications");
    expect(escalations).toHaveTextContent("25");
    expect(tile("Escalations")).toBe(escalations.querySelector(".stat"));
  });

  it("puts the economics row in one Live economics card, for an Operator only", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByText("Cost per conversation");
    const row = screen.getByRole("region", { name: "Live economics" });
    expect(within(row).getByRole("heading", { level: 2, name: "Live economics" })).toBeInTheDocument();
    expect(within(row).getByText("Cost per conversation")).toBeInTheDocument();
    expect(within(row).getByText("Conversations")).toBeInTheDocument();
    expect(within(row).getByText("Latency p50")).toBeInTheDocument();
  });

  it("shows a Recruiter the Signals card and no Live economics card", async () => {
    mockApi({ ...api, "GET /api/auth/me": recruiterMe });
    renderRoute("/overview");
    await loaded();
    expect(screen.getByRole("region", { name: "Signals" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Live economics" })).not.toBeInTheDocument();
    expect(screen.queryByText("Live economics")).not.toBeInTheDocument();
  });
});

describe("Overview all-zero charts (critique 2)", () => {
  it("says so instead of drawing an empty plot, and keeps the table view", async () => {
    const zero = { knockout: 0, rejection: 0, abandoned: 0, withdrawn: 0 };
    mockApi({ ...api, "GET /api/metrics/results": { ...results, funnel: { ...results.funnel, outcomes: zero } } });
    renderRoute("/overview");
    await loaded();
    const card = screen.getByRole("region", { name: "Applications by Outcome" });
    expect(within(card).getByText("No Applications have closed in this period.")).toBeInTheDocument();
    expect(card.querySelector(".recharts-surface")).toBeNull();
    expect(within(card).getByText("Table view")).toBeInTheDocument();
    const table = within(card).getByRole("table", { name: "Applications by Outcome (table)", hidden: true });
    expect(within(table).getByText("Knockout").closest("tr")).toHaveTextContent("0");
    // The pipeline has data and still draws.
    expect(screen.getByRole("region", { name: "Pipeline" }).querySelector(".recharts-surface")).not.toBeNull();
  });

  it("says so for a pipeline with no open Applications", async () => {
    mockApi({ ...api, "GET /api/metrics/results": { ...results, funnel: { ...results.funnel, open_by_stage: {} } } });
    renderRoute("/overview");
    await loaded();
    const card = screen.getByRole("region", { name: "Pipeline" });
    expect(within(card).getByText("No open Applications right now.")).toBeInTheDocument();
    expect(card.querySelector(".recharts-surface")).toBeNull();
    expect(within(card).getByText("Table view")).toBeInTheDocument();
  });
});

describe("Overview filters (critique 4)", () => {
  it("hides the dataset field on the Economics tab until Source is replay", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("heading", { name: "Model mix" });
    expect(screen.queryByLabelText("Comparison dataset")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "replay" } });
    const field = await screen.findByLabelText("Comparison dataset");
    expect(field).not.toHaveAttribute("placeholder");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "live" } });
    await waitFor(() => expect(screen.queryByLabelText("Comparison dataset")).not.toBeInTheDocument());
  });

  it("shows the dataset field, with a hint, on the Comparison tab", async () => {
    mockApi(api);
    renderRoute("/overview?tab=comparison");
    const field = await screen.findByLabelText("Comparison dataset");
    expect(field).not.toHaveAttribute("placeholder");
    expect(field).toHaveAccessibleDescription(/compare-2026-10-12/);
  });
});

describe("Overview stylesheet (critique 3, 5)", () => {
  const css = operatorCss();
  const media = (query: string) => [...css.matchAll(new RegExp(`@media \\(${query}\\) \\{([\\s\\S]*?)\n\\}`, "g"))].map((m) => m[1]).join("\n");

  it("keeps three tile columns from 48rem (768px) up", () => {
    expect(media("min-width: 48rem")).toMatch(/\.op-tiles\.op-tiles--3\s*\{[^}]*repeat\(3, minmax\(0, 1fr\)\)/);
  });

  it("aligns the chart grid to the start so a short card does not stretch", () => {
    expect(css).toMatch(/\.op-charts\s*\{[^}]*align-items:\s*start/);
  });

  it("aligns the filter row to the start, so hints do not push the labels onto different baselines", () => {
    expect(css).toMatch(/\.op-filters__row\s*\{[^}]*align-items:\s*start/);
  });

  it("splits the grouped figures with seams from the tokens", () => {
    expect(css).toMatch(/\.op-tiles--joined[^{]*\{[^}]*var\(--seam\)/);
  });
});

describe("Overview Economics tab grouped figures and link affordance", () => {
  it("groups the embedded figures into Cost and Usage cards without the two tiles the row shows", async () => {
    mockApi(api);
    renderRoute("/overview");
    await screen.findByRole("heading", { name: "Model mix" });
    const cost = screen.getByRole("region", { name: "Cost" });
    const usage = screen.getByRole("region", { name: "Usage" });
    expect(within(cost).queryByText("Cost per conversation")).not.toBeInTheDocument();
    for (const label of ["Cost per Qualified Application", "Cost per Interview", "Total spend"]) expect(within(cost).getByText(label)).toBeInTheDocument();
    for (const label of ["Cache hit rate", "Tokens in / out", "Errors"]) expect(within(usage).getByText(label)).toBeInTheDocument();
    expect(within(usage).queryByText("Latency p50")).not.toBeInTheDocument();
    expect(screen.getAllByText("Cost per conversation")).toHaveLength(1);
    expect(screen.getAllByText("Latency p50")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("keeps the signal links named by their figure and label", async () => {
    mockApi(api);
    renderRoute("/overview");
    await loaded();
    expect(screen.getByRole("link", { name: /25\s*Escalations/ })).toHaveAttribute("href", "/queue");
    expect(screen.getByRole("link", { name: /3\s*Qualified, waiting/ })).toHaveAttribute("href", "/applications");
  });

  it("styles the signal links as links without hover: an underlined label and an arrow", () => {
    const css = operatorCss();
    expect(css).toMatch(/\.op-figure \.stat-tile__label\s*\{[^}]*text-decoration:\s*underline/);
    expect(css).toMatch(/\.op-figure \.stat-tile__label::after\s*\{[^}]*content:\s*"\\2192"/);
  });

  it("joins four figures into 2 columns from 48rem and one row from 64rem, with seams", () => {
    const css = operatorCss();
    expect(css).toMatch(/\.op-tiles--joined\.op-tiles--4[^{]*\{[^}]*border-(top|inline-start):\s*1px solid var\(--seam\)/);
  });
});
