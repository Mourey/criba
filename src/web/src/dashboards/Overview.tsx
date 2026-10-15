import { useQuery } from "@tanstack/react-query";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client.ts";
import { canSeeEconomics, useAuth } from "../auth/context.ts";
import { BarCard } from "../charts/BarCards.tsx";
import { StatTile } from "../charts/StatTile.tsx";
import { formatNumber } from "../lib/format.ts";
import { OUTCOMES, outcomeLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/operator.ts";
import "../styles/surfaces/operator.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";
import {
  COMPARISON_TAB_LABEL,
  ComparisonPanel,
  ECONOMICS_TAB_LABEL,
  EconomicsFilters,
  EconomicsPanel,
  EconomicsRow,
  useEconomicsFilters,
} from "./Economics.tsx";

/** Data refreshes every 15 s (React Query pauses it in a hidden tab). A replayed dataset and the comparison never poll. */
const REFRESH_MS = 15_000;

type TabId = "economics" | "comparison";
const TABS: readonly { id: TabId; label: string }[] = [
  { id: "economics", label: ECONOMICS_TAB_LABEL },
  { id: "comparison", label: COMPARISON_TAB_LABEL },
];

interface ByLocation {
  location_id: string;
  applications: number;
  qualified: number;
  knockouts: number;
}

/**
 * Tabs that follow `?tab=`. The Tabs primitive is uncontrolled (only `defaultValue`), so this is the WAI-ARIA
 * pattern by hand: roving tabindex, and selection follows focus (Left, Right, Home and End).
 */
function OverviewTabs({ active, onSelect, children }: { active: TabId; onSelect: (id: TabId) => void; children: ReactNode }) {
  const base = useId();
  const tabs = useRef<Record<string, HTMLButtonElement | null>>({});
  const tabId = (id: TabId) => `${base}-tab-${id}`;
  const panelId = `${base}-panel`;

  const moveTo = (index: number) => {
    const next = TABS[(index + TABS.length) % TABS.length];
    onSelect(next.id);
    tabs.current[next.id]?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const target = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    moveTo(target);
  };

  return (
    <div className="op-tabs">
      <div className="ui-tabs__list" role="tablist" aria-label={copy.tabsLabel}>
        {TABS.map((t, index) => (
          <button
            key={t.id}
            ref={(el) => {
              tabs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={tabId(t.id)}
            className="ui-tabs__trigger"
            data-state={active === t.id ? "active" : undefined}
            aria-selected={active === t.id}
            aria-controls={panelId}
            tabIndex={active === t.id ? 0 : -1}
            onClick={() => onSelect(t.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="ui-tabs__panel" role="tabpanel" id={panelId} aria-labelledby={tabId(active)} tabIndex={0}>
        {children}
      </div>
    </div>
  );
}

/**
 * The Operator's Overview: the pipeline, Outcomes and signals from the Results endpoint (open to Recruiters too, so
 * a Recruiter who opens the URL sees these), then, for an Operator only, the economics row and the Economics and
 * Comparison tabs. Values update in place every 15 s: the page keys on `isPending` (first load), never on a refetch.
 */
export function Overview() {
  const { role } = useAuth();
  const isOperator = canSeeEconomics(role);
  const [params, setParams] = useSearchParams();
  const tab: TabId = params.get("tab") === "comparison" ? "comparison" : "economics";
  const filters = useEconomicsFilters();

  const results = useQuery({ queryKey: ["overview", "results"], queryFn: () => api.getResults(), refetchInterval: REFRESH_MS });
  const r = results.data;
  const byLocation: ByLocation[] = r?.by_location ?? [];
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.listLocations(), enabled: byLocation.length > 0 });
  const locationName = (id: string) => locations.data?.items.find((x) => x.id === id)?.name ?? id;

  const selectTab = (id: TabId) => {
    const next = new URLSearchParams(params);
    next.set("tab", id);
    setParams(next, { replace: true });
  };

  const pipeline = copy.PIPELINE_GROUPS.map((group) => ({
    label: group.label,
    value: group.stages.reduce((sum, stage) => sum + (r?.funnel.open_by_stage[stage] ?? 0), 0),
  }));
  const outcomes = OUTCOMES.map((o) => ({ label: outcomeLabel[o], value: r?.funnel.outcomes[o] ?? 0 }));
  const locationColumns: TableColumn<ByLocation>[] = [
    { key: "location", header: copy.byLocationColumns.location, render: (l) => locationName(l.location_id) },
    { key: "applications", header: copy.byLocationColumns.applications, mono: true, align: "end", render: (l) => formatNumber(l.applications) },
    { key: "qualified", header: copy.byLocationColumns.qualified, mono: true, align: "end", render: (l) => formatNumber(l.qualified) },
    { key: "knockouts", header: copy.byLocationColumns.knockouts, mono: true, align: "end", render: (l) => formatNumber(l.knockouts) },
  ];

  return (
    <div className="op-overview">
      <div className="page-head op-page-head">
        <h1>{copy.heading}</h1>
        {r && <span className="op-period">{copy.period(r.from, r.to)}</span>}
      </div>

      {results.isRefetchError && (
        <p className="op-refresh" role="status">
          {copy.refreshFailed(results.dataUpdatedAt)}
        </p>
      )}

      {results.isError && !r && <ErrorState message={copy.loadFailed(results.error.message)} onRetry={() => void results.refetch()} />}

      {(results.isPending || r) && (
        <Card title={copy.signalsTitle} flush>
          {results.isPending && (
            <div className="op-tiles op-tiles--3 op-tiles--joined" aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} shape="block" />
              ))}
            </div>
          )}
          {r && (
            <div className="op-tiles op-tiles--3 op-tiles--joined">
              <Link className="op-figure" to="/queue">
                <StatTile label={copy.signalEscalations} value={formatNumber(r.escalations)} />
              </Link>
              <Link className="op-figure" to="/queue">
                <StatTile label={copy.signalTakeovers} value={formatNumber(r.takeovers)} />
              </Link>
              <Link className="op-figure" to="/applications">
                <StatTile label={copy.signalQualifiedWaiting} value={formatNumber(r.qualified_waiting)} hint={copy.signalQualifiedWaitingHint} />
              </Link>
            </div>
          )}
        </Card>
      )}

      {isOperator && <EconomicsRow refetchMs={REFRESH_MS} />}

      {results.isPending && (
        <div className="op-skeleton">
          <Skeleton shape="block" label={copy.loadingPipeline} />
        </div>
      )}

      {r && r.applications === 0 && <p className="op-empty">{copy.empty}</p>}

      {r && r.applications > 0 && (
        <div className="op-charts">
          <BarCard
            title={copy.pipelineTitle}
            caption={copy.pipelineCaption}
            labelHeader={copy.pipelineLabelHeader}
            empty={copy.pipelineEmpty}
            ordinal
            horizontal
            data={pipeline}
          />
          <BarCard
            title={copy.outcomesTitle}
            caption={copy.outcomesCaption}
            labelHeader={copy.outcomesLabelHeader}
            empty={copy.outcomesEmpty}
            horizontal
            data={outcomes}
          />
        </div>
      )}

      {byLocation.length > 0 && (
        <Card title={copy.byLocationTitle} flush>
          <Table
            caption={copy.byLocationTitle}
            hideCaption
            columns={locationColumns}
            rows={byLocation}
            rowKey={(l) => l.location_id}
            empty={null}
          />
        </Card>
      )}

      {isOperator && (
        <OverviewTabs active={tab} onSelect={selectTab}>
          <div className="op-stack">
            <EconomicsFilters filters={filters} show={tab === "comparison" ? "dataset" : "economics"} />
            {tab === "economics" ? (
              <EconomicsPanel filters={filters} embedded refetchMs={REFRESH_MS} />
            ) : (
              <ComparisonPanel dataset={filters.dataset} />
            )}
          </div>
        </OverviewTabs>
      )}
    </div>
  );
}
