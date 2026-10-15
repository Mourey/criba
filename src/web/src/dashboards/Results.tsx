import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client.ts";
import { BarCard } from "../charts/BarCards.tsx";
import { formatDuration, formatNumber, formatPercent } from "../lib/format.ts";
import { OUTCOMES, SCREENING_STAGES, countryLabel, outcomeLabel, stageLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/recruiter.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Field } from "../components/ui/Field.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import "../components/ui/ui.css";

interface ByLocation {
  location_id: string;
  applications: number;
  qualified: number;
  knockouts: number;
}

/** A figure with its label and, where there is one, a hint. Built on Card; the design system has no Stat. */
function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <div className="rp-stat">
        <strong className="rp-stat__value">{value}</strong>
        <span className="rp-stat__label">{label}</span>
        {hint && <div className="rp-sub">{hint}</div>}
      </div>
    </Card>
  );
}

const TILES = 6;

/** Results dashboard (Client view): the funnel and where Applications stop. No cost data. */
export function Results() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [locationId, setLocationId] = useState("");
  const [country, setCountry] = useState<"" | "ES" | "MX">("");

  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.listLocations() });
  const results = useQuery({
    queryKey: ["results", from, to, locationId, country],
    queryFn: () =>
      api.getResults({
        from: from || undefined,
        to: to || undefined,
        location_id: locationId || undefined,
        country: country || undefined,
      }),
    // A changed filter keeps the figures on screen (marked busy) until the new ones arrive.
    placeholderData: keepPreviousData,
  });

  const r = results.data;
  const open = r ? Object.values(r.funnel.open_by_stage).reduce((a, b) => a + b, 0) : 0;
  const locationName = (id: string) => locations.data?.items.find((x) => x.id === id)?.name ?? id;
  const locationColumns: TableColumn<ByLocation>[] = [
    { key: "location", header: copy.resultsColumns.location, render: (l) => locationName(l.location_id) },
    { key: "applications", header: copy.resultsColumns.applications, mono: true, align: "end", render: (l) => formatNumber(l.applications) },
    { key: "qualified", header: copy.resultsColumns.qualified, mono: true, align: "end", render: (l) => formatNumber(l.qualified) },
    { key: "knockouts", header: copy.resultsColumns.knockouts, mono: true, align: "end", render: (l) => formatNumber(l.knockouts) },
  ];

  return (
    <>
      <div className="page-head rp-page-head">
        <h1>{copy.resultsTitle}</h1>
        {r && <span className="muted">{copy.resultsRange(r.from, r.to)}</span>}
      </div>

      <form className="rp-filters" aria-label={copy.resultsFilterForm} onSubmit={(e) => e.preventDefault()}>
        <Card>
          <div className="rp-filters__row">
            <Field label={copy.resultsFrom} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            <Field label={copy.resultsTo} type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            <label className="rp-field">
              {copy.resultsLocation}
              <select className="rp-select" value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                <option value="">{copy.resultsAllLocations}</option>
                {locations.data?.items.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="rp-field">
              {copy.resultsCountry}
              <select className="rp-select" value={country} onChange={(e) => setCountry(e.target.value as "" | "ES" | "MX")}>
                <option value="">{copy.resultsBothCountries}</option>
                <option value="ES">{countryLabel.ES}</option>
                <option value="MX">{countryLabel.MX}</option>
              </select>
            </label>
          </div>
        </Card>
      </form>

      {results.isError && (
        <div className="rp-error">
          <ErrorState message={copy.resultsLoadFailed(results.error.message)} onRetry={() => void results.refetch()} />
        </div>
      )}

      {results.isPending && (
        <div className="rp-tiles" aria-hidden="true">
          {Array.from({ length: TILES }, (_, i) => (
            <Skeleton key={i} shape="block" />
          ))}
        </div>
      )}

      {r && r.applications === 0 && (
        <div className="rp-empty">
          <p className="rp-empty__title">{copy.resultsEmpty}</p>
          <p className="rp-empty__hint">{copy.resultsEmptyHint}</p>
        </div>
      )}

      {r && r.applications > 0 && (
        <div className="rp-results" aria-busy={results.isPlaceholderData}>
          <div className="rp-tiles">
            <Stat label="Applications" value={formatNumber(r.applications)} />
            <Stat
              label="Time to qualify (median)"
              value={formatDuration(r.time_to_qualify_seconds.p50)}
              hint={`95th percentile ${formatDuration(r.time_to_qualify_seconds.p95)}`}
            />
            <Stat label="Qualified, waiting" value={formatNumber(r.qualified_waiting)} hint="Unclaimed for 14 days without an Approval" />
            <Stat
              label="Review overturn rate"
              value={formatPercent(r.reviews.overturn_rate)}
              hint={`${r.reviews.overturned} Overturned, ${r.reviews.upheld} Upheld, of ${r.reviews.requested} Reviews`}
            />
            <Stat label="Takeovers" value={formatNumber(r.takeovers)} />
            <Stat label="Escalations" value={formatNumber(r.escalations)} />
          </div>

          <div className="rp-charts">
            <BarCard
              title="Applications by Outcome"
              caption="Open Applications, then each Outcome. Qualified Applications are still open."
              horizontal
              data={[
                { label: "Open", value: open },
                ...OUTCOMES.map((o) => ({ label: outcomeLabel[o], value: r.funnel.outcomes[o] })),
              ]}
            />
            <BarCard
              title="Open Applications by stage"
              ordinal
              horizontal
              data={[
                ...SCREENING_STAGES,
                "qualified" as const,
                "approved" as const,
                "interview_booked" as const,
              ].map((s) => ({ label: stageLabel[s], value: r.funnel.open_by_stage[s] ?? 0 }))}
            />
            <BarCard
              title="Drop-off by stage"
              caption="Abandoned and Stalled Applications, by the stage they stopped at."
              ordinal
              data={SCREENING_STAGES.map((s) => ({ label: stageLabel[s], value: r.dropoff_by_stage[s] ?? 0 }))}
            />
            <BarCard
              title="Knockouts by criterion"
              data={[
                { label: "Zone", value: r.knockouts_by_criterion.zone },
                { label: "Driver's license", value: r.knockouts_by_criterion.driver_license },
              ]}
            />
          </div>

          {r.by_location && r.by_location.length > 0 && (
            <Card title={copy.resultsByLocation} flush>
              <Table
                caption={copy.resultsByLocation}
                hideCaption
                columns={locationColumns}
                rows={r.by_location}
                rowKey={(l) => l.location_id}
                empty={null}
              />
            </Card>
          )}
        </div>
      )}
    </>
  );
}
