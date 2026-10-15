import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { api } from "../api/client.ts";
import type { Location, Slot, SlotTimes } from "../api/types.ts";
import { addMinutes, localToUtcIso, utcToLocalParts } from "../lib/tz.ts";
import * as copy from "../lib/labels/recruiter.ts";
import "../styles/surfaces/recruiter.css";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { ErrorState } from "../components/ui/ErrorState.tsx";
import { Field } from "../components/ui/Field.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import { Tag } from "../components/ui/Tag.tsx";
import "../components/ui/ui.css";

interface Draft extends SlotTimes {
  label: string;
}

export function Slots() {
  const queryClient = useQueryClient();
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.listLocations() });
  const [chosen, setChosen] = useState<string>("");
  const locationList = locations.data?.items ?? [];
  const location: Location | undefined = locationList.find((l) => l.id === chosen) ?? locationList[0];

  const [date, setDate] = useState("");
  const [start, setStart] = useState("10:00");
  const [end, setEnd] = useState("10:30");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);

  // No placeholder: a changed Location shows the skeleton, never the previous Location's slots.
  const slots = useQuery({
    queryKey: ["slots", location?.id],
    queryFn: () => api.listSlots({ location_id: location?.id }),
    enabled: Boolean(location),
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["slots"] });
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : copy.slotsFailed);

  const publish = useMutation({
    mutationFn: () =>
      api.createSlots(
        location!.id,
        drafts.map(({ starts_at, ends_at }) => ({ starts_at, ends_at })),
      ),
    onSuccess: () => {
      setError(null);
      setDrafts([]);
      refresh();
    },
    onError: fail,
  });
  const cancel = useMutation({
    mutationFn: (slotId: string) => api.cancelSlot(slotId),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: fail,
  });
  const move = useMutation({
    mutationFn: (v: { id: string; times: SlotTimes }) => api.updateSlot(v.id, v.times),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: fail,
  });

  const addDraft = (event: FormEvent) => {
    event.preventDefault();
    if (!location || !date || end <= start) {
      setError(end <= start ? copy.slotsEndBeforeStart : copy.slotsChooseDate);
      return;
    }
    setError(null);
    const starts_at = localToUtcIso(date, start, location.timezone);
    const ends_at = localToUtcIso(date, end, location.timezone);
    setDrafts((d) => [...d, { starts_at, ends_at, label: `${date} ${start}-${end}` }]);
  };

  const byDay = new Map<string, Slot[]>();
  for (const s of slots.data?.items ?? []) {
    const day = location ? utcToLocalParts(s.starts_at, location.timezone).date : s.starts_at.slice(0, 10);
    byDay.set(day, [...(byDay.get(day) ?? []), s]);
  }
  const days = [...byDay.keys()].sort();

  return (
    <>
      <div className="page-head rp-page-head">
        <h1>{copy.slotsTitle}</h1>
        <label className="rp-field">
          {copy.slotsLocation}
          <select className="rp-select" value={location?.id ?? ""} onChange={(e) => setChosen(e.target.value)}>
            {locationList.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <p className="rp-alert" role="alert">
          {error}
        </p>
      )}

      {locations.isError && (
        <div className="rp-error">
          <ErrorState message={copy.slotsLocationsFailed(locations.error.message)} onRetry={() => void locations.refetch()} />
        </div>
      )}

      <div className="rp-stack">
        <Card title={copy.slotsPublishTitle}>
          <div className="rp-stack">
            <p className="rp-muted">
              {copy.slotsTimeZoneNote}
              {location ? ` (${location.timezone})` : ""}.
            </p>
            <form className="rp-slot-form" onSubmit={addDraft}>
              <Field label={copy.slotsDate} type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
              <Field
                label={copy.slotsStarts}
                type="time"
                required
                value={start}
                onChange={(e) => {
                  setStart(e.target.value);
                  setEnd(addMinutes(e.target.value, 30));
                }}
              />
              <Field label={copy.slotsEnds} type="time" required value={end} onChange={(e) => setEnd(e.target.value)} />
              <Button type="submit" disabled={!location}>
                {copy.slotsAddToBatch}
              </Button>
            </form>
            {drafts.length > 0 && (
              <>
                <ul className="rp-slot-list" aria-label={copy.slotsBatchList}>
                  {drafts.map((d, i) => (
                    <DraftRow key={`${d.starts_at}-${i}`} label={d.label} onRemove={() => setDrafts((all) => all.filter((_, j) => j !== i))} />
                  ))}
                </ul>
                <div className="rp-actions">
                  <Button variant="primary" onClick={() => publish.mutate()} disabled={publish.isPending}>
                    {copy.slotsPublishCount(drafts.length)}
                  </Button>
                </div>
              </>
            )}
          </div>
        </Card>

        <Card title={copy.slotsHeading(location?.name)}>
          {locations.isPending ? (
            <ListSkeleton />
          ) : !location ? (
            !locations.isError && (
              <div className="rp-empty">
                <p className="rp-empty__title">{copy.slotsNoLocations}</p>
                <p className="rp-empty__hint">{copy.slotsNoLocationsHint}</p>
              </div>
            )
          ) : slots.isPending ? (
            <ListSkeleton />
          ) : slots.isError ? (
            <ErrorState message={copy.slotsLoadFailed(slots.error.message)} onRetry={() => void slots.refetch()} />
          ) : days.length === 0 ? (
            <div className="rp-empty">
              <p className="rp-empty__title">{copy.slotsEmpty}</p>
              <p className="rp-empty__hint">{copy.slotsEmptyHint}</p>
            </div>
          ) : (
            <div className="rp-stack">
              {days.map((day) => (
                <div key={day} className="rp-day">
                  <h3 className="rp-day__title">{day}</h3>
                  <ul className="rp-slot-list">
                    {byDay.get(day)!.map((s) => (
                      <SlotRow
                        key={s.id}
                        slot={s}
                        timezone={location.timezone}
                        onCancel={() => cancel.mutate(s.id)}
                        onMove={(times) => move.mutate({ id: s.id, times })}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

/** The shapes of the rows about to load; decorative, so hidden from assistive technology. */
function ListSkeleton() {
  return (
    <div className="rp-skeleton rp-skeleton--flush" aria-hidden="true">
      {Array.from({ length: 3 }, (_, i) => (
        <Skeleton key={i} shape="block" />
      ))}
    </div>
  );
}

function DraftRow(props: { label: string; onRemove: () => void }) {
  const labelId = useId();
  return (
    <li className="rp-slot">
      <span id={labelId} className="rp-slot__label">
        {props.label}
      </span>
      <Button onClick={props.onRemove} aria-describedby={labelId}>
        {copy.slotsRemove}
      </Button>
    </li>
  );
}

function SlotRow(props: { slot: Slot; timezone: string; onCancel: () => void; onMove: (times: SlotTimes) => void }) {
  const { slot, timezone } = props;
  const labelId = useId();
  const [editing, setEditing] = useState(false);
  const from = utcToLocalParts(slot.starts_at, timezone);
  const to = utcToLocalParts(slot.ends_at, timezone);
  const [date, setDate] = useState(from.date);
  const [start, setStart] = useState(from.time);
  const [end, setEnd] = useState(to.time);

  return (
    <li className="rp-slot">
      <span className="rp-slot__what">
        <span id={labelId} className="rp-slot__label">
          {slot.local_label ?? `${from.time}-${to.time}`}
        </span>
        <Tag tone={slot.status === "booked" ? "info" : "neutral"}>{copy.statusLabel[slot.status]}</Tag>
        {slot.booking && (
          <span className="rp-sub">
            {slot.booking.candidate_name ?? copy.slotsCandidateFallback} ({copy.slotsBookedVia(slot.booking.booked_via)})
          </span>
        )}
      </span>
      {slot.status === "open" && !editing && (
        <span className="rp-actions">
          <Button onClick={() => setEditing(true)} aria-describedby={labelId}>
            {copy.slotsMove}
          </Button>
          <Button variant="danger" onClick={props.onCancel} aria-describedby={labelId}>
            {copy.slotsCancelSlot}
          </Button>
        </span>
      )}
      {editing && (
        <form
          className="rp-actions"
          onSubmit={(e) => {
            e.preventDefault();
            props.onMove({ starts_at: localToUtcIso(date, start, timezone), ends_at: localToUtcIso(date, end, timezone) });
            setEditing(false);
          }}
        >
          <input className="rp-input" aria-label={copy.slotsNewDate} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <input className="rp-input" aria-label={copy.slotsNewStart} type="time" value={start} onChange={(e) => setStart(e.target.value)} />
          <input className="rp-input" aria-label={copy.slotsNewEnd} type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
          <Button variant="primary" type="submit">
            {copy.slotsSave}
          </Button>
          <Button onClick={() => setEditing(false)}>{copy.slotsCancel}</Button>
        </form>
      )}
    </li>
  );
}
