import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client.ts";
import type { ApplicationFilters, ApplicationSummary, Flag, Outcome, Stage } from "../api/types.ts";
import { FlagTags, StageOutcome, StepTimes } from "../components/Flags.tsx";
import { candidateName, formatDateTime, formatDuration } from "../lib/format.ts";
import { FLAGS, OUTCOMES, SCREENING_STAGES, STAGES, countryLabel, flagLabel, outcomeLabel, stageLabel } from "../lib/labels.ts";
import * as copy from "../lib/labels/recruiter.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Field } from "../components/ui/Field.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Table, type TableColumn } from "../components/ui/Table.tsx";
import { Tag } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";

/** How long typing in the search box pauses before the filter (and the query) changes. */
const SEARCH_DEBOUNCE_MS = 300;

/** Rows the skeleton draws while the first page loads. */
const SKELETON_ROWS = 6;

/** The URL parameters behind the "More filters" disclosure. */
const HIDDEN_FILTERS = ["country", "created_from", "created_to", "stage", "outcome", "flag"] as const;

function readFilters(params: URLSearchParams): ApplicationFilters {
  const filters: ApplicationFilters = {};
  const one = (key: string) => params.get(key) || undefined;
  filters.q = one("q");
  filters.location_id = one("location_id");
  filters.country = one("country") as ApplicationFilters["country"];
  filters.status = one("status") as ApplicationFilters["status"];
  filters.claimed_by = one("claimed_by");
  filters.created_from = one("created_from");
  filters.created_to = one("created_to");
  filters.sort = (one("sort") as ApplicationFilters["sort"]) ?? "-updated_at";
  const stage = params.getAll("stage") as Stage[];
  const outcome = params.getAll("outcome") as Outcome[];
  const flag = params.getAll("flag") as Flag[];
  if (stage.length) filters.stage = stage;
  if (outcome.length) filters.outcome = outcome;
  if (flag.length) filters.flag = flag;
  return filters;
}

const columns: TableColumn<ApplicationSummary>[] = [
  {
    key: "candidate",
    header: copy.applicationsColumns.candidate,
    render: (a) => (
      <>
        <Link className="rp-link" to={`/applications/${a.id}`}>
          {candidateName(a.candidate)}
        </Link>
        {a.candidate.phone && (
          <div className="rp-sub" title={a.candidate.phone}>
            {a.candidate.phone}
          </div>
        )}
      </>
    ),
  },
  {
    key: "locations",
    header: copy.applicationsColumns.locations,
    // One line with an ellipsis keeps rows short; the full text stays in the DOM and in the title.
    render: (a) => {
      const names = a.locations.map((l) => l.name).join(", ") || copy.applicationsNoLocation;
      return (
        <span className="rp-truncate" title={names}>
          {names}
        </span>
      );
    },
  },
  {
    key: "stage",
    header: copy.applicationsColumns.stage,
    // The cell clips at its column width; the title carries the whole Stage and Outcome.
    render: (a) => (
      <span title={a.outcome ? `${stageLabel[a.stage]}, ${outcomeLabel[a.outcome]}` : stageLabel[a.stage]}>
        <StageOutcome stage={a.stage} outcome={a.outcome} />
      </span>
    ),
  },
  {
    key: "claim",
    header: copy.applicationsColumns.claim,
    render: (a) => (
      <>
        {a.claim ? (
          <>
            <span>{a.claim.user_name}</span> {a.claim.idle && <Tag tone="warn">{copy.applicationsClaimedIdle}</Tag>}
          </>
        ) : (
          <span className="rp-sub rp-sub--lead">{copy.applicationsUnclaimed}</span>
        )}
        {a.control === "human" && (
          <div className="rp-sub" title={copy.applicationsTakeover(a.control_user_name)}>
            {copy.applicationsTakeover(a.control_user_name)}
          </div>
        )}
      </>
    ),
  },
  { key: "in_stage", header: copy.applicationsColumns.inStage, mono: true, align: "end", render: (a) => formatDuration(a.time_in_stage_seconds) },
  { key: "total", header: copy.applicationsColumns.total, mono: true, align: "end", render: (a) => formatDuration(a.total_seconds) },
  { key: "steps", header: copy.applicationsColumns.steps, render: (a) => <StepTimes durations={a.stage_durations_seconds} order={SCREENING_STAGES} /> },
  {
    key: "flags",
    header: copy.applicationsColumns.flags,
    // The cell clips at its column width; the title lists every Flag.
    render: (a) => (
      <span title={a.flags.length ? a.flags.map((f) => flagLabel[f]).join(", ") : undefined}>
        <FlagTags flags={a.flags} />
      </span>
    ),
  },
  { key: "updated", header: copy.applicationsColumns.updated, mono: true, render: (a) => formatDateTime(a.updated_at) },
];

export function Applications() {
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const foundId = useId();
  const moreId = useId();

  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.listLocations() });
  const list = useInfiniteQuery({
    queryKey: ["applications", params.toString()],
    queryFn: ({ pageParam, signal }) => api.listApplications({ ...filters, cursor: pageParam, limit: 50 }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // A changed filter keeps the rows on screen (marked busy) until the new page arrives.
    placeholderData: keepPreviousData,
  });

  // The box keeps its own text; the `q` filter in the URL follows it after a pause. A change
  // to `q` from elsewhere (Clear filters, back/forward) is copied into the box.
  const urlQ = params.get("q") ?? "";
  const [search, setSearch] = useState(urlQ);
  const committedQ = useRef(urlQ);
  useEffect(() => {
    if (urlQ !== committedQ.current) {
      committedQ.current = urlQ;
      setSearch(urlQ);
    }
  }, [urlQ]);
  useEffect(() => {
    if (search === committedQ.current) return;
    const timer = setTimeout(() => {
      committedQ.current = search;
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (search) next.set("q", search);
          else next.delete("q");
          return next;
        },
        { replace: true },
      );
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search, setParams]);

  const setOne = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const toggleMany = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    const current = next.getAll(key);
    next.delete(key);
    const updated = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
    for (const v of updated) next.append(key, v);
    setParams(next, { replace: true });
  };

  // The disclosure holds Country, the two dates and the three chip groups. Each of them that is set counts
  // once, however many chips it has. It opens by itself when the URL gains one (first load, back/forward, a
  // link) and then follows the Recruiter's own clicks; closing it with filters set keeps the count on the button.
  const hiddenActive = HIDDEN_FILTERS.filter((key) => params.getAll(key).some(Boolean)).length;
  const [moreOpen, setMoreOpen] = useState(hiddenActive > 0);
  const hadHidden = useRef(hiddenActive > 0);
  useEffect(() => {
    if (hiddenActive > 0 && !hadHidden.current) setMoreOpen(true);
    hadHidden.current = hiddenActive > 0;
  }, [hiddenActive]);

  const clearAll = () => setParams(new URLSearchParams(), { replace: true });

  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const total = list.data?.pages[0]?.total;
  const hasFilters = [...params.keys()].some((k) => k !== "sort");
  const hasParams = [...params.keys()].length > 0;

  return (
    <>
      <div className="page-head rp-page-head">
        <h1>{copy.applicationsTitle}</h1>
        <span id={foundId} className="muted" aria-live="polite">
          {total !== undefined && copy.applicationsFound(total)}
        </span>
      </div>

      <form className="rp-filters rp-filters--applications" role="search" aria-label={copy.applicationsFilterForm} onSubmit={(e) => e.preventDefault()}>
        <Card>
          <div className="rp-filters__row rp-filters__row--main">
            <Field
              className="rp-filters__search"
              label={copy.filterSearch}
              type="search"
              placeholder={copy.filterSearchPlaceholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <SelectField label={copy.filterLocation} value={params.get("location_id") ?? ""} onChange={(v) => setOne("location_id", v)}>
              <option value="">{copy.filterAllLocations}</option>
              {locations.data?.items.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </SelectField>
            <SelectField label={copy.filterStatus} value={params.get("status") ?? ""} onChange={(v) => setOne("status", v)}>
              <option value="">{copy.filterStatusAny}</option>
              <option value="open">{copy.filterStatusOpen}</option>
              <option value="closed">{copy.filterStatusClosed}</option>
            </SelectField>
            <SelectField label={copy.filterClaim} value={params.get("claimed_by") ?? ""} onChange={(v) => setOne("claimed_by", v)}>
              <option value="">{copy.filterClaimAny}</option>
              <option value="me">{copy.filterClaimMine}</option>
              <option value="none">{copy.filterClaimNone}</option>
            </SelectField>
            <SelectField label={copy.filterSort} value={filters.sort ?? "-updated_at"} onChange={(v) => setOne("sort", v)}>
              {copy.SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </SelectField>
            <div className="rp-filters__toggle">
              <Button variant="secondary" aria-expanded={moreOpen} aria-controls={moreId} onClick={() => setMoreOpen((open) => !open)}>
                {copy.filterMore}
                {hiddenActive > 0 && (
                  <>
                    {" "}
                    <span className="rp-more-count" aria-hidden="true">
                      {hiddenActive}
                    </span>
                    <span className="ui-sr-only">{copy.filterMoreActive(hiddenActive)}</span>
                  </>
                )}
              </Button>
            </div>
            {hasFilters && !list.isError && (
              <div className="rp-actions rp-filters__actions">
                <Button variant="quiet" onClick={clearAll}>
                  {copy.filterClear}
                </Button>
              </div>
            )}
          </div>

          {/* Stays mounted when closed: the controls keep their state and every label is still in the document. */}
          <div id={moreId} className="rp-filters__more" hidden={!moreOpen}>
            <div className="rp-filters__row rp-filters__row--more">
              <SelectField label={copy.filterCountry} value={params.get("country") ?? ""} onChange={(v) => setOne("country", v)}>
                <option value="">{copy.filterBothCountries}</option>
                <option value="ES">{countryLabel.ES}</option>
                <option value="MX">{countryLabel.MX}</option>
              </SelectField>
              <Field label={copy.filterCreatedFrom} type="date" value={params.get("created_from") ?? ""} onChange={(e) => setOne("created_from", e.target.value)} />
              <Field label={copy.filterCreatedTo} type="date" value={params.get("created_to") ?? ""} onChange={(e) => setOne("created_to", e.target.value)} />
            </div>
            <div className="rp-filters__chips">
              <ChipGroup legend={copy.filterStage} values={STAGES} selected={params.getAll("stage")} label={(s) => stageLabel[s]} onToggle={(v) => toggleMany("stage", v)} />
              <ChipGroup legend={copy.filterOutcome} values={OUTCOMES} selected={params.getAll("outcome")} label={(o) => outcomeLabel[o]} onToggle={(v) => toggleMany("outcome", v)} />
              <ChipGroup legend={copy.filterFlags} values={FLAGS} selected={params.getAll("flag")} label={(f) => flagLabel[f]} onToggle={(v) => toggleMany("flag", v)} />
            </div>
          </div>
        </Card>
      </form>

      {list.isError && (
        <div className="rp-error">
          <ErrorState message={copy.applicationsLoadFailed(list.error.message)} onRetry={() => void list.refetch()} />
          {hasParams && (
            <div className="rp-actions">
              <Button onClick={clearAll}>{copy.filterClear}</Button>
            </div>
          )}
        </div>
      )}

      {(list.isPending || list.data) && (
        // The page's own scroll region: Table's wrapper does not scroll here (recruiter.css).
        <div
          className="rp-scroll"
          role="region"
          aria-label={copy.applicationsTableRegion}
          tabIndex={0}
          aria-busy={list.isPending || list.isPlaceholderData}
        >
          {list.isPending ? (
            <div className="rp-skeleton" aria-hidden="true">
              {Array.from({ length: SKELETON_ROWS }, (_, i) => (
                <Skeleton key={i} shape="block" />
              ))}
            </div>
          ) : (
            <Table
              caption={copy.applicationsTitle}
              hideCaption
              columns={columns}
              rows={items}
              rowKey={(a) => a.id}
              empty={
                <>
                  <p className="rp-empty__title">{copy.applicationsEmpty}</p>
                  <p className="rp-empty__hint">{hasFilters ? copy.applicationsEmptyFiltered : copy.applicationsEmptyNone}</p>
                </>
              }
            />
          )}
        </div>
      )}
      {list.hasNextPage && (
        <div className="rp-actions rp-more-rows">
          <Button onClick={() => void list.fetchNextPage()} loading={list.isFetchingNextPage} aria-describedby={foundId}>
            {copy.applicationsLoadMore}
          </Button>
        </div>
      )}
    </>
  );
}

function SelectField(props: { label: string; value: string; onChange: (value: string) => void; children: ReactNode }) {
  return (
    <label className="rp-field">
      {props.label}
      <select className="rp-select" value={props.value} onChange={(e) => props.onChange(e.target.value)}>
        {props.children}
      </select>
    </label>
  );
}

function ChipGroup<T extends string>(props: {
  legend: string;
  values: readonly T[];
  selected: string[];
  label: (value: T) => string;
  onToggle: (value: T) => void;
}) {
  return (
    <fieldset className="rp-chips">
      <legend className="rp-legend">{props.legend}</legend>
      {props.values.map((v) => (
        <label key={v} className="rp-chip">
          <input type="checkbox" checked={props.selected.includes(v)} onChange={() => props.onToggle(v)} />
          {props.label(v)}
        </label>
      ))}
    </fieldset>
  );
}
