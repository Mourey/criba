import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useParams } from "react-router";
import { api, ApiError } from "../api/client.ts";
import type { PublicBookingConfirmation, PublicSlot } from "../api/types.ts";
// Primitives by file, not through ui/index.ts: the index loads the motion provider too, and this page
// is public, so it mounts nothing that belongs to the signed-in shell.
import { Button } from "../components/ui/Button.tsx";
import { Card } from "../components/ui/Card.tsx";
import { Icon } from "../components/ui/Icon.tsx";
import { Skeleton } from "../components/ui/Skeleton.tsx";
import "../components/ui/ui.css";
import { COPY, dayMonth, failureKind, formatExpiry, GONE_TITLE, LINK_TITLE, LOAD_FAILED, LOAD_FAILED_TITLE, LOAD_RETRY, pageLanguage, splitLabel } from "../lib/labels/booking.ts";
import "../styles/surfaces/booking.css";

// The booking page is Candidate-facing: Spanish for es-ES and es-MX, English for en (copy in
// lib/labels/booking.ts). It shows the first name and open slots only (spec 003), never anything
// else about the Application.

interface Chip {
  slot: PublicSlot;
  time: string;
}

interface Day {
  day: string;
  chips: Chip[];
}

interface SlotGroup {
  key: string;
  name: string;
  address: string | null;
  days: Day[];
}

/**
 * One group per Location (name and address), in the order of each Location's first slot. Inside a
 * group the slots keep the API's order and split into runs of the same day; none is hidden. A label with
 * no " · " has no day and stays whole.
 */
function groupSlots(slots: readonly PublicSlot[]): SlotGroup[] {
  const groups = new Map<string, SlotGroup>();
  for (const slot of slots) {
    const address = slot.address ?? null;
    const key = `${slot.location_name}\n${address ?? ""}`;
    const group = groups.get(key) ?? { key, name: slot.location_name, address, days: [] };
    const { day, time } = splitLabel(slot.local_label);
    const last = group.days[group.days.length - 1];
    if (last && last.day === day) last.chips.push({ slot, time });
    else group.days.push({ day, chips: [{ slot, time }] });
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** The server's own text, kept only when the response carried one (a body-less failure has none). */
function serverText(error: unknown): string {
  return error instanceof ApiError && error.code !== "internal" ? error.message : "";
}

export function Book() {
  const { token = "" } = useParams();
  const queryClient = useQueryClient();
  const [slotId, setSlotId] = useState("");
  const [confirmation, setConfirmation] = useState<PublicBookingConfirmation | null>(null);
  // The failed booking, and the label of the time it was for (the page names it when it was taken).
  const [bookFailure, setBookFailure] = useState<{ error: unknown; label: string | null; where: string | null } | null>(null);
  const confirmationRef = useRef<HTMLElement>(null);
  // Set at once, before React shows the pending state, so a second press in the same moment sends nothing.
  const sending = useRef(false);

  const page = useQuery({
    queryKey: ["booking", token],
    queryFn: () => api.getBookingPage(token),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const language = pageLanguage(page.data?.language);
  const t = COPY[language];

  const book = useMutation({
    mutationFn: (id: string) => api.bookFromPage(token, id),
    onSuccess: (result) => {
      setBookFailure(null);
      setConfirmation(result);
    },
    onSettled: () => {
      sending.current = false;
    },
    onError: (e, id) => {
      const lost = page.data?.slots.find((s) => s.slot_id === id);
      setBookFailure({ error: e, label: lost?.local_label ?? null, where: lost?.location_name ?? null });
      if (failureKind(e) === "taken") {
        setSlotId("");
        void queryClient.invalidateQueries({ queryKey: ["booking", token] });
      }
    },
  });

  // The confirmation replaces the form, so the focused Confirm button is gone: move focus to it.
  useEffect(() => {
    if (confirmation) confirmationRef.current?.focus();
  }, [confirmation]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!slotId || sending.current) return;
    sending.current = true;
    book.mutate(slotId);
  };

  const pick = (id: string) => {
    setSlotId(id);
    setBookFailure(null);
  };

  const problem = bookFailure?.error ?? (page.isError ? page.error : null);
  const kind = problem === null ? null : failureKind(problem);
  const loading = !page.data && (page.isPending || page.isFetching);
  const gone = kind === "gone" && serverText(problem) !== "";

  // The heading and the tab title say what the page is for in this state. Where the language is unknown
  // they speak both (a link that cannot be used), or Spanish (a load that failed).
  const unusable = kind === "gone" || kind === "invalid";
  const failedLoad = !loading && !unusable && !page.data;
  let heading: ReactNode = t.title;
  let tabTitle: string = t.title;
  if (unusable) {
    const title = kind === "gone" ? GONE_TITLE : LINK_TITLE;
    heading = (
      <>
        <span lang="es">{title.es}</span> · <span lang="en">{title.en}</span>
      </>
    );
    tabTitle = `${title.es} · ${title.en}`;
  } else if (failedLoad) {
    heading = LOAD_FAILED_TITLE;
    tabTitle = LOAD_FAILED_TITLE;
  } else if (confirmation) {
    heading = t.doneTitle;
    tabTitle = t.doneTitle;
  }
  useEffect(() => {
    const before = document.title;
    document.title = tabTitle;
    return () => {
      document.title = before;
    };
  }, [tabTitle]);

  let body;
  if (loading) {
    body = (
      <div className="booking__skeleton">
        <Skeleton shape="block" label={COPY.es.loading} />
        <div aria-hidden="true" className="booking__skeleton">
          <Skeleton shape="block" />
          <Skeleton shape="block" />
        </div>
      </div>
    );
  } else if (kind === "gone" || kind === "invalid") {
    body = gone ? (
      <div className="booking__alert" role="alert">
        <Icon name="warning" size="lg" />
        <p>{serverText(problem)}</p>
      </div>
    ) : (
      <div className="booking__alert" role="alert">
        <Icon name="warning" size="lg" />
        <div className="booking__alert-text">
          <p lang="es">{COPY.es[kind === "gone" ? "gone" : "invalid"]}</p>
          <p lang="en">{COPY.en[kind === "gone" ? "gone" : "invalid"]}</p>
        </div>
      </div>
    );
  } else if (!page.data) {
    body = (
      <div className="booking__alert" role="alert">
        <Icon name="warning" size="lg" />
        <div className="booking__alert-text">
          <p>{LOAD_FAILED}</p>
          <Button onClick={() => void page.refetch()}>{LOAD_RETRY}</Button>
        </div>
      </div>
    );
  } else if (confirmation) {
    body = (
      <section ref={confirmationRef} role="status" tabIndex={-1} className="booking__confirmation">
        <Card>
          <div className="booking__done">
            <p>{confirmation.message}</p>
            <p className="booking__when">
              <strong>{confirmation.interview.local_label}</strong>
              <span>
                {confirmation.interview.location_name}
                {confirmation.interview.address ? `, ${confirmation.interview.address}` : ""}
              </span>
            </p>
            <a className="booking__download" href={confirmation.ics_url} download>
              {t.download}
            </a>
          </div>
        </Card>
      </section>
    );
  } else {
    const current = page.data.current_booking;
    const until = formatExpiry(page.data.expires_at, page.data.language);
    const notice = kind === "taken" ? t.taken(bookFailure?.label ?? null, bookFailure?.where ?? null) : kind ? t.generic : null;
    const noticeBlock = notice && (
      <p className="booking__alert" role="alert">
        {notice}
      </p>
    );
    const chosen = page.data.slots.find((s) => s.slot_id === slotId);
    const pageLang = page.data.language;
    const month = (day: Day) => dayMonth(day.chips[0].slot.starts_at, day.day, pageLang);
    body = (
      <form className="booking__form" onSubmit={onSubmit}>
        <p className="booking__lede">
          {t.hello(page.data.first_name)} {t.pick}
        </p>
        {current && (
          <p className="booking__notice">
            {t.current} <strong>{current.local_label}</strong>, {current.location_name}. {t.change}
          </p>
        )}
        {page.data.slots.length === 0 ? (
          <>
            {noticeBlock}
            <p className="booking__empty">{t.none}</p>
          </>
        ) : (
          <>
            {groupSlots(page.data.slots).map((group) => (
              <fieldset key={group.key} className="booking__group">
                <legend className="booking__legend">
                  <strong>{group.name}</strong>
                  {group.address ? (
                    <>
                      {" "}
                      <span className="booking__address">{group.address}</span>
                    </>
                  ) : null}
                </legend>
                {group.days.map((day, index) => (
                  <div key={`${day.day}\n${index}`} className="booking__day">
                    {day.day && (
                      <p className="booking__day-label">
                        {day.day}
                        {month(day) && <> <span className="booking__month">{month(day)}</span></>}
                      </p>
                    )}
                    <div className="booking__times">
                      {day.chips.map(({ slot, time }) => (
                        <label key={slot.slot_id} className="booking__slot">
                          <input className="booking__radio" type="radio" name="slot" value={slot.slot_id} checked={slotId === slot.slot_id} onChange={() => pick(slot.slot_id)} />
                          {day.day && <span className="ui-sr-only">{day.day} · </span>}
                          <strong>{time}</strong>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </fieldset>
            ))}
            <div className="booking__bar">
              {noticeBlock}
              {!notice && (
                <p className="booking__hint" aria-live="polite">
                  {chosen ? t.chosen(chosen.local_label, chosen.location_name) : t.pickFirst}
                </p>
              )}
              <Button type="submit" variant="primary" disabled={!slotId} loading={book.isPending}>
                {t.confirm}
              </Button>
            </div>
          </>
        )}
        {until && <p className="booking__valid">{t.validUntil(until)}</p>}
      </form>
    );
  }

  return (
    <main className="booking" lang={language}>
      <meta name="referrer" content="no-referrer" />
      <meta name="robots" content="noindex" />
      <h1 className="booking__title">{heading}</h1>
      <div aria-busy={loading}>{body}</div>
    </main>
  );
}
