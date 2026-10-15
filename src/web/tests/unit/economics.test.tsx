import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import type { Role } from "../../src/api/types.ts";
import { AuthContext, type AuthValue } from "../../src/auth/context.ts";
import { ComparisonPanel, EconomicsPanel, economicsQueryOptions } from "../../src/dashboards/Economics.tsx";
import { comparison, economics, operatorMe } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// The lazy dashboard modules and Recharts' first render are warmed in tests/warm-lazy-routes.tsx.

describe("Economics dashboard", () => {
  const api = {
    "GET /api/auth/me": operatorMe,
    "GET /api/metrics/economics": economics,
    "GET /api/metrics/comparison": comparison,
  };

  it("shows cost per conversation, Qualified Application and Interview, tokens, latency, mix, errors and spend", async () => {
    mockApi(api);
    renderRoute("/economics");
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(screen.getByText("$0.0031")).toBeInTheDocument();
    expect(screen.getByText("$0.0402")).toBeInTheDocument();
    expect(screen.getByText("$0.1409")).toBeInTheDocument();
    expect(screen.getByText("71.0%")).toBeInTheDocument();
    const latency = screen.getAllByText("Latency p50").find((el) => el.closest(".stat"))!.closest(".stat")!;
    expect(latency).toHaveTextContent("1.7 s");
    expect(latency).toHaveTextContent("p95 3.9 s");
    expect(screen.getByText("0.12% of runs, 9 retries")).toBeInTheDocument();
    const mix = screen.getByRole("heading", { name: "Model mix" }).closest("section")!;
    expect(within(mix).getByText("anthropic/claude-haiku-5.5")).toBeInTheDocument();
    const spend = screen.getByRole("table", { name: "Spend by environment (table)", hidden: true });
    expect(within(spend).getByText("Simulation").closest("tr")).toHaveTextContent("$1.40");
  });

  it("replays the model comparison dataset", async () => {
    const mock = mockApi(api);
    renderRoute("/economics");
    const heading = await screen.findByRole("heading", { name: "Model comparison" });
    const section = heading.closest("section")!;
    expect(await within(section).findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
    expect(within(section).getByText("anthropic/claude-sonnet-5.5").closest("tr")).toHaveTextContent("98%");

    fireEvent.change(screen.getByLabelText("Comparison dataset"), { target: { value: "compare-2026-10-12" } });
    await waitFor(() => expect(mock.find("GET", "/api/metrics/comparison").at(-1)!.query.get("dataset")).toBe("compare-2026-10-12"));
  });

  it("switches the source to replay and requests that dataset", async () => {
    const mock = mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "replay" } });
    expect(await screen.findByText("Name a comparison dataset to replay it.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Comparison dataset"), { target: { value: "compare-2026-10-12" } });
    await waitFor(() => {
      const q = mock.find("GET", "/api/metrics/economics").at(-1)!.query;
      expect(q.get("source")).toBe("replay");
      expect(q.get("dataset")).toBe("compare-2026-10-12");
    });
  });

  it("filters by environment", async () => {
    const mock = mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "sim" } });
    await waitFor(() => expect(mock.find("GET", "/api/metrics/economics").at(-1)!.query.get("environment")).toBe("sim"));
  });

  it("handles a missing comparison dataset", async () => {
    mockApi({ ...api, "GET /api/metrics/comparison": error(404, "not_found", "No such dataset.") });
    renderRoute("/economics");
    expect(await screen.findByText(/No comparison dataset found/)).toBeInTheDocument();
  });
});

// Phase 4 (operator): per-panel states, the embedded panel, and the role re-check in every exported
// panel. Appended; the five tests above stay as they were.

const liveFilters = { from: "", to: "", environment: "" as const, source: "live" as const, dataset: "" };
const forever = () => new Promise<object>(() => undefined);
const skeletons = (root: ParentNode = document) => root.querySelectorAll(".ui-skeleton").length;

function auth(role: Role): AuthValue {
  return {
    me: operatorMe,
    loading: false,
    error: null,
    refetch: () => undefined,
    realRole: "operator",
    role,
    isPreviewingRecruiter: role === "recruiter",
    setPreviewRecruiter: () => undefined,
    login: async () => operatorMe,
    logout: async () => undefined,
  };
}

function Harness({ role, client, children }: { role: Role; client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={auth(role)}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });

describe("Economics page states", () => {
  const api = {
    "GET /api/auth/me": operatorMe,
    "GET /api/metrics/economics": economics,
    "GET /api/metrics/comparison": comparison,
  };

  it("shows a Skeleton, not loading text, while the economics load", async () => {
    mockApi({ ...api, "GET /api/metrics/economics": forever });
    renderRoute("/economics");
    expect(await screen.findByRole("heading", { level: 1, name: "Economics" })).toBeInTheDocument();
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/Loading economics/);
  });

  it("shows an error with Retry that sends exactly one more economics request", async () => {
    let n = 0;
    const mock = mockApi({ ...api, "GET /api/metrics/economics": () => (n++ === 0 ? error(500, "internal", "boom") : economics) });
    renderRoute("/economics");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load economics. boom");
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(1);
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(2);
  });

  it("keeps the h1, the form and the Comparison section when the economics fail", async () => {
    mockApi({ ...api, "GET /api/metrics/economics": error(500, "internal", "boom") });
    renderRoute("/economics");
    await screen.findByRole("alert");
    expect(screen.getByRole("heading", { level: 1, name: "Economics" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Filter economics" })).toBeInTheDocument();
    expect(await screen.findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
  });

  it("shows a Skeleton in the Comparison section while it loads", async () => {
    mockApi({ ...api, "GET /api/metrics/comparison": forever });
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    const section = screen.getByRole("heading", { name: "Model comparison" }).closest("section")!;
    await waitFor(() => expect(skeletons(section)).toBeGreaterThan(0));
  });

  it("explains a missing comparison dataset without an error or a Retry", async () => {
    mockApi({ ...api, "GET /api/metrics/comparison": error(404, "not_found", "No such dataset.") });
    renderRoute("/economics");
    const section = (await screen.findByRole("heading", { name: "Model comparison" })).closest("section")!;
    expect(await within(section).findByText("No comparison dataset found.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("names the dataset in the empty state", async () => {
    mockApi({ ...api, "GET /api/metrics/comparison": error(404, "not_found", "No such dataset.") });
    renderRoute("/economics");
    await screen.findByText(/No comparison dataset found/);
    fireEvent.change(screen.getByLabelText("Comparison dataset"), { target: { value: "compare-x" } });
    expect(await screen.findByText("No comparison dataset found named compare-x.")).toBeInTheDocument();
  });

  it("gives another Comparison error a Retry that sends exactly one more request", async () => {
    let n = 0;
    const mock = mockApi({ ...api, "GET /api/metrics/comparison": () => (n++ === 0 ? error(500, "internal", "down") : comparison) });
    renderRoute("/economics");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load the comparison. down");
    expect(mock.find("GET", "/api/metrics/comparison")).toHaveLength(1);
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/comparison")).toHaveLength(2);
  });

  it("keeps one h1 and the Model mix and comparison tables inside their cards", async () => {
    mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(await screen.findByRole("table", { name: "Model mix" })).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Model comparison" })).toBeInTheDocument();
  });
});

describe("EconomicsPanel and ComparisonPanel", () => {
  const api = {
    "GET /api/auth/me": operatorMe,
    "GET /api/metrics/economics": economics,
    "GET /api/metrics/comparison": comparison,
  };

  it("render nothing and send nothing for a Recruiter", async () => {
    const mock = mockApi(api);
    const view = render(
      <Harness role="recruiter" client={newClient()}>
        <EconomicsPanel filters={liveFilters} refetchMs={15_000} />
        <EconomicsPanel filters={liveFilters} embedded />
        <ComparisonPanel dataset="" />
      </Harness>,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(view.container).toBeEmptyDOMElement();
    expect(mock.calls).toHaveLength(0);
  });

  it("render nothing for a Recruiter even when the cache holds the figures", async () => {
    const mock = mockApi(api);
    const client = newClient();
    client.setQueryData(economicsQueryOptions(liveFilters).queryKey, economics);
    client.setQueryData(["comparison", ""], comparison);
    const view = render(
      <Harness role="recruiter" client={client}>
        <EconomicsPanel filters={liveFilters} />
        <ComparisonPanel dataset="" />
      </Harness>,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(view.container).toBeEmptyDOMElement();
    expect(document.body.textContent).not.toMatch(/\$/);
    expect(mock.calls).toHaveLength(0);
  });

  it("drop the figures when the role changes to Recruiter in the same cache, and stop asking", async () => {
    const mock = mockApi(api);
    const client = newClient();
    const panels = (
      <>
        <EconomicsPanel filters={liveFilters} refetchMs={15_000} />
        <ComparisonPanel dataset="" />
      </>
    );
    const view = render(
      <Harness role="operator" client={client}>
        {panels}
      </Harness>,
    );
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(await screen.findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
    const before = mock.calls.length;

    view.rerender(
      <Harness role="recruiter" client={client}>
        {panels}
      </Harness>,
    );
    expect(view.container).toBeEmptyDOMElement();
    expect(mock.calls).toHaveLength(before);
  });

  it("render the figures for an Operator, and the embedded panel leaves out the two tiles the Overview row shows", async () => {
    mockApi(api);
    render(
      <Harness role="operator" client={newClient()}>
        <EconomicsPanel filters={liveFilters} embedded />
      </Harness>,
    );
    expect(await screen.findByText("Cost per Qualified Application")).toBeInTheDocument();
    expect(screen.getByText("Total spend")).toBeInTheDocument();
    expect(screen.queryByText("Cost per conversation")).not.toBeInTheDocument();
    expect(screen.queryByText("Latency p50")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("render the full page tiles and no h1 when not embedded", async () => {
    mockApi(api);
    render(
      <Harness role="operator" client={newClient()}>
        <EconomicsPanel filters={liveFilters} />
      </Harness>,
    );
    expect(await screen.findByText("Cost per conversation")).toBeInTheDocument();
    expect(screen.getByText("Latency p50")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("refetch on refetchMs in place, with the same nodes and no Skeleton", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    const mock = mockApi({
      ...api,
      "GET /api/metrics/economics": () => (n++ === 0 ? economics : { ...economics, cost_usd: { ...economics.cost_usd, total: 4.2 } }),
    });
    render(
      <Harness role="operator" client={newClient()}>
        <EconomicsPanel filters={liveFilters} refetchMs={15_000} />
      </Harness>,
    );
    const total = (await screen.findByText("Total spend")).closest(".stat");
    expect(total).toHaveTextContent("$3.10");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(total).toHaveTextContent("$4.20"));
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(2);
    expect(screen.getByText("Total spend").closest(".stat")).toBe(total);
    expect(skeletons()).toBe(0);
  });

  it("do not refetch a replayed dataset on an interval", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const mock = mockApi(api);
    render(
      <Harness role="operator" client={newClient()}>
        <EconomicsPanel filters={{ ...liveFilters, source: "replay", dataset: "compare-2026-10-12" }} refetchMs={15_000} />
      </Harness>,
    );
    await screen.findByText("Cost per conversation");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(1);
  });

  it("explain a replay with no dataset and send nothing", async () => {
    const mock = mockApi(api);
    render(
      <Harness role="operator" client={newClient()}>
        <EconomicsPanel filters={{ ...liveFilters, source: "replay" }} />
      </Harness>,
    );
    expect(await screen.findByText("Name a comparison dataset to replay it.")).toBeInTheDocument();
    expect(mock.find("GET", "/api/metrics/economics")).toHaveLength(0);
  });
});

describe("Economics filters and empty charts (critique 2, 4)", () => {
  const api = {
    "GET /api/auth/me": operatorMe,
    "GET /api/metrics/economics": economics,
    "GET /api/metrics/comparison": comparison,
  };

  it("names the default period in a hint on From and To", async () => {
    mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    expect(screen.getByLabelText("From")).toHaveAccessibleDescription("Default: last 30 days");
    expect(screen.getByLabelText("To")).toHaveAccessibleDescription("Default: today");
  });

  it("keeps From at or before To, and To at or after From", async () => {
    mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    expect(screen.getByLabelText("From")).not.toHaveAttribute("max");
    expect(screen.getByLabelText("To")).not.toHaveAttribute("min");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-10-09" } });
    expect(screen.getByLabelText("From")).toHaveAttribute("max", "2026-10-09");
    expect(screen.getByLabelText("To")).toHaveAttribute("min", "2026-10-01");
  });

  it("shows the dataset field on the page as a hinted field, not a value-looking placeholder", async () => {
    mockApi(api);
    renderRoute("/economics");
    const field = await screen.findByLabelText("Comparison dataset");
    expect(field).not.toHaveAttribute("placeholder");
    expect(field).toHaveAccessibleDescription(/compare-2026-10-12/);
  });

  it("says so, and keeps the table view, when there is no spend", async () => {
    mockApi({
      ...api,
      "GET /api/metrics/economics": { ...economics, spend_by_environment: { prod: 0, sim: 0, test: 0 } },
    });
    renderRoute("/economics");
    const card = (await screen.findByRole("heading", { name: "Spend by environment" })).closest("section")!;
    expect(within(card).getByText("No spend recorded in this period.")).toBeInTheDocument();
    expect(card.querySelector(".recharts-surface")).toBeNull();
    expect(within(card).getByText("Table view")).toBeInTheDocument();
    expect(within(card).getByRole("table", { name: "Spend by environment (table)", hidden: true })).toBeInTheDocument();
  });
});

describe("Cost by model empty state", () => {
  it("says no model cost was recorded, not that there were no calls, when the calls cost nothing", async () => {
    const free = {
      ...economics,
      model_mix: economics.model_mix.map((m) => ({ ...m, cost_usd: 0 })),
    };
    mockApi({
      "GET /api/auth/me": operatorMe,
      "GET /api/metrics/economics": free,
      "GET /api/metrics/comparison": comparison,
    });
    renderRoute("/economics");
    const card = (await screen.findByRole("heading", { name: "Cost by model" })).closest("section")!;
    expect(within(card).getByText("No model cost recorded in this period.")).toBeInTheDocument();
    expect(within(card).getByText("Model spend in this period, in US dollars.")).toBeInTheDocument();
    expect(screen.queryByText("No model calls in this period.")).not.toBeInTheDocument();
    expect(card.querySelector(".recharts-surface")).toBeNull();
    // The calls are still listed in Model mix.
    const mix = screen.getByRole("heading", { name: "Model mix" }).closest("section")!;
    expect(within(mix).getByText("9,800")).toBeInTheDocument();
  });
});

describe("Economics grouped figures and the Model comparison card", () => {
  const api = {
    "GET /api/auth/me": operatorMe,
    "GET /api/metrics/economics": economics,
    "GET /api/metrics/comparison": comparison,
  };
  const stat = (root: HTMLElement, label: string) => within(root).getByText(label).closest(".stat");

  it("groups the eight tiles into a Cost card and a Usage card on /economics", async () => {
    mockApi(api);
    renderRoute("/economics");
    await screen.findByText("Cost per conversation");
    const cost = screen.getByRole("region", { name: "Cost" });
    const usage = screen.getByRole("region", { name: "Usage" });
    expect(within(cost).getByRole("heading", { level: 2, name: "Cost" })).toBeInTheDocument();
    expect(within(usage).getByRole("heading", { level: 2, name: "Usage" })).toBeInTheDocument();
    for (const label of ["Cost per conversation", "Cost per Qualified Application", "Cost per Interview", "Total spend"]) {
      expect(stat(cost, label), label).not.toBeNull();
    }
    for (const label of ["Cache hit rate", "Tokens in / out", "Latency p50", "Errors"]) {
      expect(stat(usage, label), label).not.toBeNull();
    }
    expect(stat(cost, "Total spend")).toHaveTextContent("$3.10");
    expect(stat(usage, "Latency p50")).toHaveTextContent("p95 3.9 s");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("puts the Model comparison in a Card, with the heading, the table and the 404 sentence inside", async () => {
    mockApi(api);
    renderRoute("/economics");
    const card = await screen.findByRole("region", { name: "Model comparison" });
    expect(card).toHaveClass("ui-card");
    expect(within(card).getByRole("heading", { level: 2, name: "Model comparison" })).toBeInTheDocument();
    expect(await within(card).findByText("Dataset compare-2026-10-12, 50 personas.")).toBeInTheDocument();
    expect(within(card).getByText("anthropic/claude-sonnet-5.5")).toBeInTheDocument();
  });

  it("keeps the 404 sentence inside the Model comparison card", async () => {
    mockApi({ ...api, "GET /api/metrics/comparison": error(404, "not_found", "No such dataset.") });
    renderRoute("/economics");
    const card = await screen.findByRole("region", { name: "Model comparison" });
    expect(await within(card).findByText("No comparison dataset found.")).toBeInTheDocument();
  });
});
