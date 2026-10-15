import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client.ts";
import type { ApplicationSummary } from "../api/types.ts";
import { matchByPhone } from "./phone.ts";

/** How often the list is asked again while the Application does not exist yet. */
const POLL_MS = 1000;

export interface ApplicationLookup {
  /** The Candidate's Application, once the list holds an exact match for the phone; else null. */
  application: ApplicationSummary | null;
  /** The lookup is running: the Candidate has written. Before that it is idle and asks for nothing. */
  started: boolean;
  isError: boolean;
  error: Error | null;
  /** Asks the list once more (the error state's Retry). */
  retry: () => void;
}

/**
 * Finds the Application of a Take by its phone: `GET /api/applications?q=<phone>` with the whole phone as
 * the one `q`, and an exact client-side match on `candidate.phone` (never a prefix, never the first
 * row). It is idle until `enabled` (the first Candidate message), asks again every second until a
 * match appears, and then stops. A failing list is not polled; Retry asks once.
 */
export function useApplicationLookup({ phone, enabled }: { phone: string; enabled: boolean }): ApplicationLookup {
  const query = useQuery({
    queryKey: ["stage-lookup", phone],
    queryFn: ({ signal }) => api.listApplications({ q: phone, limit: 50 }, signal),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    select: (list) => matchByPhone(list.items, phone),
    refetchInterval: (q) => (q.state.status === "error" || matchByPhone(q.state.data?.items ?? [], phone) ? false : POLL_MS),
  });
  return {
    application: query.data ?? null,
    started: enabled,
    isError: query.isError,
    error: query.error,
    retry: () => void query.refetch(),
  };
}
