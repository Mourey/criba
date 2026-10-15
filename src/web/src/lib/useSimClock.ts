import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.ts";

export interface SimClock {
  available: boolean;
  now: Date | null;
  isLoading: boolean;
}

const MINUTE_MS = 60_000;

/** How long an "unavailable" answer is kept before the clock is asked again. */
const UNAVAILABLE_STALE_MS = 60_000;

/** The instant in a clock body, or null unless it is an object whose `now` parses as a date. */
function parseNow(body: unknown): Date | null {
  if (typeof body !== "object" || body === null) return null;
  const now = (body as { now?: unknown }).now;
  if (typeof now !== "string") return null;
  const date = new Date(now);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * The sim stack's clock (GET /api/sim/clock). Any other stack answers 404, and any failure, a network
 * error or a malformed body resolves to "unavailable" instead of an error, so the one-minute
 * staleTime applies to it too. Not called by the Shell or RequireAuth.
 *
 * The sim clock is real time plus an offset (`criba/api/clock.py`), so one fetch fixes the offset and
 * `now` then moves on with the page's own time: consumers re-render at each minute boundary of the
 * clock, and `now` is the server's answer plus the time since it arrived. A new fetch replaces the
 * base: the stage invalidates ["sim-clock"] on every stream event, and the playback controls must on
 * every advance or reset.
 */
export function useSimClock(): SimClock {
  const query = useQuery({
    queryKey: ["sim-clock"],
    queryFn: async (): Promise<Date | null> => {
      try {
        return parseNow(await api.getSimClock());
      } catch {
        return null;
      }
    },
    retry: false,
    staleTime: (q) => (q.state.data === null ? UNAVAILABLE_STALE_MS : 0),
  });
  const base = query.data ?? null;
  const fetchedAt = query.dataUpdatedAt;
  // The page time of the last tick, and the answer it was taken against: `now` equals the answer
  // until the first tick, and a newer answer starts again from zero.
  const [ticked, setTicked] = useState<{ fetchedAt: number; at: number } | null>(null);
  const elapsed = ticked !== null && ticked.fetchedAt === fetchedAt ? ticked.at - fetchedAt : 0;

  useEffect(() => {
    if (base === null) return;
    // Wake just past the next minute boundary of the clock, so the minute shown is the new one.
    const wait = MINUTE_MS - ((base.getTime() + Date.now() - fetchedAt) % MINUTE_MS) + 20;
    const timer = setTimeout(() => setTicked({ fetchedAt, at: Date.now() }), wait);
    return () => clearTimeout(timer);
  }, [base, fetchedAt, ticked]);

  const now = useMemo(() => (base === null ? null : new Date(base.getTime() + elapsed)), [base, elapsed]);
  return { available: now !== null, now, isLoading: query.isLoading };
}
