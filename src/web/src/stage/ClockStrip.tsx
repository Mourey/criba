import type { ReactNode } from "react";
import { Icon } from "../components/ui/Icon.tsx";
import "../components/ui/ui.css";
import { formatDateTime } from "../lib/format.ts";
import { stage } from "../lib/labels.ts";
import { useSimClock } from "../lib/useSimClock.ts";
import "../styles/surfaces/stage.css";
import { formatZoned, type PhoneCountry } from "./phone.ts";

/**
 * The sim stack's clock, shown only when GET /api/sim/clock answered 200 with a parseable time. Any
 * other answer renders nothing, children included. `children` is the slot for the playback controls
 * (advance, reset); this strip reads the clock and never changes it.
 *
 * With a `country` the time is in that country's zone, the one the bubbles and the record use, and the
 * zone is named once, here. Without one it is UTC.
 */
export function ClockStrip({ children, country }: { children?: ReactNode; country?: PhoneCountry }) {
  const { available, now } = useSimClock();
  if (!available || !now) return null;
  const iso = now.toISOString();
  return (
    <section className="stage-clock" aria-label={stage.clockRegion}>
      <span className="stage-clock__label">
        <Icon name="clock" size="sm" />
        {stage.clockRegion}
      </span>
      <time className="stage-clock__time" dateTime={iso}>
        {country ? formatZoned(iso, country) : formatDateTime(iso)}
      </time>
      {country ? <span className="stage-clock__zone">{stage.zoneName[country]}</span> : null}
      {children}
    </section>
  );
}
