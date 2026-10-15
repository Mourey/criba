import { queryOptions, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client.ts";
import type { EconomicsMetrics, ModelComparison } from "../api/types.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { BarCard, GroupedBarCard } from "../charts/BarCards.tsx";
import { StatTile } from "../charts/StatTile.tsx";
import { formatMs, formatNumber, formatPercent, formatUsd, formatUsdAxis } from "../lib/format.ts";
import { refreshFailed } from "../lib/labels/operator.ts";
import "../styles/surfaces/operator.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Field } from "../components/ui/Field.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";

// The Overview's tab labels live here, with the other economics copy (labels/operator.ts keeps cost out).
export const ECONOMICS_TAB_LABEL = "Economics";
export const COMPARISON_TAB_LABEL = "Comparison";

export type EconomicsEnvironment = "" | "prod" | "sim" | "test";
export type EconomicsSource = "live" | "replay";

export interface EconomicsFilterValues {
  from: string;
  to: string;
  environment: EconomicsEnvironment;
  source: EconomicsSource;
  dataset: string;
}

export interface EconomicsFilterState extends EconomicsFilterValues {
  setFrom: (value: string) => void;
  setTo: (value: string) => void;
  setEnvironment: (value: EconomicsEnvironment) => void;
  setSource: (value: EconomicsSource) => void;
  setDataset: (value: string) => void;
}

const LIVE_DEFAULTS: EconomicsFilterValues = { from: "", to: "", environment: "", source: "live", dataset: "" };

const isLiveDefaults = (f: EconomicsFilterValues) =>
  f.from === "" && f.to === "" && f.environment === "" && f.source === "live" && f.dataset === "";

/** The economics query for a set of filters. The Overview's row calls it with the defaults, so the row and the
 * live Economics panel share one cache entry. The caller decides `enabled`. */
export function economicsQueryOptions(filters: EconomicsFilterValues) {
  const { from, to, environment, source, dataset } = filters;
  return queryOptions({
    queryKey: ["economics", from, to, environment, source, dataset] as const,
    queryFn: () =>
      api.getEconomics({
        from: from || undefined,
        to: to || undefined,
        environment: environment || undefined,
        source,
        dataset: source === "replay" ? dataset || undefined : undefined,
      }),
  });
}

/** A replay needs a named dataset; the live ledger needs nothing. */
const readyToAsk = (f: EconomicsFilterValues) => f.source === "live" || f.dataset !== "";

export function useEconomicsFilters(): EconomicsFilterState {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [environment, setEnvironment] = useState<EconomicsEnvironment>("");
  const [source, setSource] = useState<EconomicsSource>("live");
  const [dataset, setDataset] = useState("");
  return { from, to, environment, source, dataset, setFrom, setTo, setEnvironment, setSource, setDataset };
}

/**
 * The labelled form. `show: "economics"` is the Overview's Economics tab (the dataset field appears only for a replay),
 * `"dataset"` the Comparison tab, which only reads a dataset name, and `"all"` the /economics page, whose Comparison
 * section is always on screen and so always has its dataset field.
 */
export function EconomicsFilters({ filters, show = "all" }: { filters: EconomicsFilterState; show?: "all" | "economics" | "dataset" }) {
  const { role } = useAuth();
  if (!canSeeEconomics(role)) return null;
  const periodFields = show !== "dataset";
  const datasetField = show === "all" || show === "dataset" || filters.source === "replay";
  return (
    <form className="op-filters" aria-label="Filter economics" onSubmit={(ev) => ev.preventDefault()}>
      <Card>
        <div className="op-filters__row">
          {periodFields && (
            <>
              <Field
                label="From"
                type="date"
                value={filters.from}
                max={filters.to || undefined}
                hint="Default: last 30 days"
                onChange={(ev) => filters.setFrom(ev.target.value)}
              />
              <Field
                label="To"
                type="date"
                value={filters.to}
                min={filters.from || undefined}
                hint="Default: today"
                onChange={(ev) => filters.setTo(ev.target.value)}
              />
              <label className="op-field">
                Environment
                <select className="op-select" value={filters.environment} onChange={(ev) => filters.setEnvironment(ev.target.value as EconomicsEnvironment)}>
                  <option value="">All</option>
                  <option value="prod">Production</option>
                  <option value="sim">Simulation</option>
                  <option value="test">Test</option>
                </select>
              </label>
              <label className="op-field">
                Source
                <select className="op-select" value={filters.source} onChange={(ev) => filters.setSource(ev.target.value as EconomicsSource)}>
                  <option value="live">Live ledger</option>
                  <option value="replay">Replay a comparison dataset</option>
                </select>
              </label>
            </>
          )}
          {datasetField && (
            <Field
              label="Comparison dataset"
              value={filters.dataset}
              hint="The name of a dataset, for example compare-2026-10-12"
              onChange={(ev) => filters.setDataset(ev.target.value)}
            />
          )}
        </div>
      </Card>
    </form>
  );
}

const shortModel = (model: string) => model.split("/").pop() ?? model;

function TileSkeleton({ count, joined = false }: { count: number; joined?: boolean }) {
  return (
    <div className={joined ? "op-tiles op-tiles--3 op-tiles--joined" : count === 3 || count === 6 ? "op-tiles op-tiles--3" : "op-tiles"} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} shape="block" />
      ))}
    </div>
  );
}

/**
 * The Overview's economics row: cost per conversation, conversations and latency, from the live ledger with no
 * filters. It asks the role first and renders nothing, and asks the server nothing, unless the role is Operator:
 * a disabled query still hands back cached data, so the role decides before any data is read.
 */
export function EconomicsRow({ refetchMs }: { refetchMs?: number }) {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  const economics = useQuery({
    ...economicsQueryOptions(LIVE_DEFAULTS),
    enabled: allowed,
    refetchInterval: allowed && refetchMs ? refetchMs : false,
  });
  if (!allowed) return null;

  const e = economics.data;
  return (
    <Card title="Live economics" flush>
      {economics.isPending && <TileSkeleton count={3} joined />}
      {economics.isError && !e && (
        <div className="op-card-pad">
          <ErrorState message={`Could not load economics. ${economics.error.message}`} onRetry={() => void economics.refetch()} />
        </div>
      )}
      {e && (
        <>
          <div className="op-tiles op-tiles--3 op-tiles--joined">
            <StatTile label="Cost per conversation" value={formatUsd(e.cost_usd.per_conversation)} />
            <StatTile label="Conversations" value={formatNumber(e.conversations)} />
            <StatTile label="Latency p50" value={formatMs(e.latency_ms.p50)} hint={`p95 ${formatMs(e.latency_ms.p95)}`} />
          </div>
          {economics.isError && (
            <div className="op-card-pad">
              <p className="op-refresh" role="status">
                {refreshFailed(economics.dataUpdatedAt)}
              </p>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

const mixColumns: TableColumn<EconomicsMetrics["model_mix"][number]>[] = [
  { key: "model", header: "Model" },
  { key: "kind", header: "Use" },
  { key: "calls", header: "Calls", mono: true, align: "end", render: (m) => formatNumber(m.calls) },
  { key: "share", header: "Share", mono: true, align: "end", render: (m) => formatPercent(m.share, 0) },
  { key: "cost", header: "Cost", mono: true, align: "end", render: (m) => formatUsd(m.cost_usd) },
];

/**
 * The economics figures for a set of filters. `embedded` is the Overview's tab: no "Cost per conversation" and no
 * "Latency p50" (the row above already shows them) and never an h1. `refetchMs` refreshes a live source in place; a
 * replayed dataset is fixed and never polls. Renders nothing, and sends nothing, unless the role is Operator.
 */
export function EconomicsPanel({ filters, embedded = false, refetchMs }: { filters: EconomicsFilterValues; embedded?: boolean; refetchMs?: number }) {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  // Embedded in the Overview, the row reads (and polls) the default key already: this panel does not poll it twice,
  // and leaves the error and its Retry to the row, so a failure shows one alert, not two.
  const polledByRow = embedded && isLiveDefaults(filters);
  const economics = useQuery({
    ...economicsQueryOptions(filters),
    enabled: allowed && readyToAsk(filters),
    refetchInterval: allowed && filters.source === "live" && refetchMs && !polledByRow ? refetchMs : false,
  });
  if (!allowed) return null;

  const e = economics.data;
  const tileClass = embedded ? "op-tiles op-tiles--3 op-tiles--joined" : "op-tiles op-tiles--4 op-tiles--joined";
  const tiles = e && (
    <>
      <Card title="Cost" flush>
        <div className={tileClass}>
          {!embedded && (
            <StatTile label="Cost per conversation" value={formatUsd(e.cost_usd.per_conversation)} hint={`${formatNumber(e.conversations)} conversations`} />
          )}
          <StatTile label="Cost per Qualified Application" value={formatUsd(e.cost_usd.per_qualified)} />
          <StatTile label="Cost per Interview" value={formatUsd(e.cost_usd.per_interview)} />
          <StatTile label="Total spend" value={formatUsd(e.cost_usd.total)} />
        </div>
      </Card>
      <Card title="Usage" flush>
        <div className={tileClass}>
          <StatTile
            label="Cache hit rate"
            value={formatPercent(e.tokens.cache_hit_rate)}
            hint={`${formatNumber(e.tokens.cache_read)} read, ${formatNumber(e.tokens.cache_write)} written`}
          />
          <StatTile
            label="Tokens in / out"
            value={`${formatNumber(e.tokens.input)} / ${formatNumber(e.tokens.output)}`}
            hint={`${formatNumber(Math.round(e.tokens.per_turn_input ?? 0))} input per turn`}
          />
          {!embedded && <StatTile label="Latency p50" value={formatMs(e.latency_ms.p50)} hint={`p95 ${formatMs(e.latency_ms.p95)}`} />}
          <StatTile
            label="Errors"
            value={formatNumber(e.errors.count)}
            hint={`${formatPercent(e.errors.rate, 2)} of runs, ${formatNumber(e.errors.retries)} retries`}
          />
        </div>
      </Card>
    </>
  );

  return (
    <div className="op-stack">
      {filters.source === "replay" && !filters.dataset && <p className="op-notice">Name a comparison dataset to replay it.</p>}
      {economics.isError && !e && !polledByRow && (
        <ErrorState message={`Could not load economics. ${economics.error.message}`} onRetry={() => void economics.refetch()} />
      )}
      {economics.isError && !e && polledByRow && <p className="op-notice">These figures load with the row above, which has the error and its Retry.</p>}
      {economics.isPending && economics.fetchStatus !== "idle" && <TileSkeleton count={embedded ? 6 : 8} />}
      {e && (
        <>
          {economics.isError && !polledByRow && (
            <p className="op-refresh" role="status">
              {refreshFailed(economics.dataUpdatedAt)}
            </p>
          )}
          {tiles}
          <div className="op-charts">
            <BarCard
              title="Spend by environment"
              caption="Simulation versus production spend, in US dollars."
              format={formatUsd}
              axisFormat={formatUsdAxis}
              horizontal
              empty="No spend recorded in this period."
              labelHeader="Environment"
              data={[
                { label: "Production", value: e.spend_by_environment.prod },
                { label: "Simulation", value: e.spend_by_environment.sim },
                { label: "Test", value: e.spend_by_environment.test },
              ]}
            />
            <BarCard
              title="Cost by model"
              caption="Model spend in this period, in US dollars."
              format={formatUsd}
              axisFormat={formatUsdAxis}
              horizontal
              empty="No model cost recorded in this period."
              labelHeader="Model"
              data={e.model_mix.map((m) => ({ label: shortModel(m.model), value: m.cost_usd }))}
            />
          </div>
          <Card title="Model mix" flush>
            <Table caption="Model mix" hideCaption columns={mixColumns} rows={e.model_mix} rowKey={(m) => `${m.model}-${m.kind}`} empty="No model calls in this period." />
          </Card>
        </>
      )}
    </div>
  );
}

const comparisonColumns: TableColumn<ModelComparison["models"][number]>[] = [
  { key: "model", header: "Model" },
  { key: "runs", header: "Runs", mono: true, align: "end", render: (m) => formatNumber(m.runs) },
  { key: "p50", header: "Latency p50", mono: true, align: "end", render: (m) => formatMs(m.latency_ms.p50) },
  { key: "p95", header: "Latency p95", mono: true, align: "end", render: (m) => formatMs(m.latency_ms.p95) },
  { key: "cost", header: "Cost per screening", mono: true, align: "end", render: (m) => formatUsd(m.cost_per_screening_usd) },
  { key: "failures", header: "Validation failures", mono: true, align: "end", render: (m) => formatNumber(m.validation_failures) },
  { key: "escalations", header: "Escalations", mono: true, align: "end", render: (m) => formatNumber(m.escalations) },
  { key: "accuracy", header: "Outcome accuracy", mono: true, align: "end", render: (m) => formatPercent(m.outcome_accuracy, 0) },
];

/**
 * The model comparison for a dataset. A 404 is an explained empty state, not an error. It is a fixed dataset, so it
 * never polls. Renders nothing, and sends nothing, unless the role is Operator.
 */
export function ComparisonPanel({ dataset }: { dataset: string }) {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  const comparison = useQuery({
    queryKey: ["comparison", dataset],
    queryFn: () => api.getModelComparison(dataset || undefined),
    enabled: allowed,
    retry: false,
  });
  if (!allowed) return null;

  const c = comparison.data;
  const missing = comparison.error instanceof ApiError && comparison.error.status === 404;
  return (
    <div className="op-stack">
      <Card title="Model comparison" flush>
        {comparison.isPending && (
          <div className="op-card-pad">
            <Skeleton shape="block" label="Loading the comparison" />
          </div>
        )}
        {comparison.isError && missing && (
          <p className="op-card-pad op-card-text">No comparison dataset found{dataset ? ` named ${dataset}` : ""}.</p>
        )}
        {comparison.isError && !missing && (
          <div className="op-card-pad">
            <ErrorState message={`Could not load the comparison. ${comparison.error.message}`} onRetry={() => void comparison.refetch()} />
          </div>
        )}
        {c && (
          <>
            <p className="op-card-pad op-sub">
              Dataset {c.dataset}, {c.personas} personas.
            </p>
            <div className="op-table-card" role="region" aria-label="Model comparison table" tabIndex={0}>
              <Table caption="Model comparison" hideCaption columns={comparisonColumns} rows={c.models} rowKey={(m) => m.model} empty="No models in this dataset." />
            </div>
          </>
        )}
      </Card>
      {c && (
        <div className="op-charts">
          <GroupedBarCard
            title="Latency by model"
            format={formatMs}
            labelHeader="Model"
            series={[
              { key: "p50", name: "p50" },
              { key: "p95", name: "p95" },
            ]}
            data={c.models.map((m) => ({ label: shortModel(m.model), p50: m.latency_ms.p50 ?? 0, p95: m.latency_ms.p95 ?? 0 }))}
          />
          <BarCard
            title="Cost per screening"
            format={formatUsd}
            axisFormat={formatUsdAxis}
            labelHeader="Model"
            data={c.models.map((m) => ({ label: shortModel(m.model), value: m.cost_per_screening_usd }))}
          />
        </div>
      )}
    </div>
  );
}

/** Economics dashboard: Operators only (ADR 0019). The server refuses it for Recruiters too. */
export function Economics() {
  const { role } = useAuth();
  const allowed = canSeeEconomics(role);
  const filters = useEconomicsFilters();
  // The period beside the h1 comes from the same query the panel runs (one cache entry, one request).
  const range = useQuery({ ...economicsQueryOptions(filters), enabled: allowed && readyToAsk(filters) });

  if (!allowed) {
    return (
      <div className="op-guard">
        <h1>Economics</h1>
        <p role="alert">The economics views are for Operators only.</p>
      </div>
    );
  }

  return (
    <>
      <div className="page-head op-page-head">
        <h1>Economics</h1>
        {range.data && (
          <span className="op-period">
            {range.data.from} to {range.data.to}
          </span>
        )}
      </div>
      <EconomicsFilters filters={filters} />
      <div className="op-stack op-stack--page">
        <EconomicsPanel filters={filters} />
        <ComparisonPanel dataset={filters.dataset} />
      </div>
    </>
  );
}
