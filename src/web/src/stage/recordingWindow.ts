// The recording window: the hours of the Mexico City day in which a recording may run, because quiet
// hours are 22:00 to 09:00 local (src/backend/criba/scheduler/quiet.py:17-18; ADR 0046 exempts the
// Approval turn while the Candidate is active). Mexico City has had no daylight saving since October
// 2022, but the zone goes through Intl, so the answer stays right either way.

export const RECORDING_WINDOW = { zone: "America/Mexico_City", startHour: 9, endHour: 22 } as const;

export interface WindowReading {
  /** True from 09:00 (inclusive) to 22:00 (exclusive) Mexico City. */
  inside: boolean;
  /** The Mexico City clock time, "HH:mm" on a 24-hour clock. */
  time: string;
}

const clock = new Intl.DateTimeFormat("en-US", {
  timeZone: RECORDING_WINDOW.zone,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Where `now` falls against the recording window, read in Mexico City whatever the browser's zone. */
export function recordingWindow(now: Date): WindowReading {
  const parts = clock.formatToParts(now);
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return {
    inside: hour >= RECORDING_WINDOW.startHour && hour < RECORDING_WINDOW.endHour,
    time: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
  };
}
