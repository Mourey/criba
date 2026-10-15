import { useEffect, useRef } from "react";
import type { StreamEvent } from "../api/types.ts";

export type StreamHandler = (event: StreamEvent) => void;

/**
 * Subscribe to a server-sent event stream (never WebSockets, spec 003). Named events are
 * `message`, `control`, `event` and `queue`; each `data:` line is a JSON StreamEvent.
 * Does nothing where EventSource does not exist (tests, old browsers).
 *
 * The backend starts a new stream at its newest row and closes each stream after a few minutes;
 * the browser reconnects with `Last-Event-ID`. Anything written between the page's own fetch and
 * the first `open` is not replayed, so `onOpen` (called on every open, including reconnects) is
 * where the caller refetches.
 */
export function useEventStream(
  url: string | null,
  names: readonly string[],
  onEvent: StreamHandler,
  onOpen?: () => void,
): void {
  const handler = useRef(onEvent);
  const opened = useRef(onOpen);
  useEffect(() => {
    handler.current = onEvent;
    opened.current = onOpen;
  });
  const key = names.join(",");

  useEffect(() => {
    if (!url || typeof EventSource === "undefined") return;
    const source = new EventSource(url, { withCredentials: true });
    const listener = (raw: Event) => {
      const data = (raw as MessageEvent | undefined)?.data;
      if (typeof data !== "string") return;
      try {
        handler.current(JSON.parse(data) as StreamEvent);
      } catch {
        // A malformed frame is ignored; the next frame or a refetch repairs the view.
      }
    };
    const onOpenListener = () => opened.current?.();
    const listened = key.split(",");
    for (const name of listened) source.addEventListener(name, listener);
    source.addEventListener("open", onOpenListener);
    return () => {
      for (const name of listened) source.removeEventListener(name, listener);
      source.removeEventListener("open", onOpenListener);
      source.close();
    };
  }, [url, key]);
}
