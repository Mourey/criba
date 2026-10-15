// Time zone helpers without a date library. Slots are entered in the Location's local time and
// sent to the API as UTC instants.

function offsetMinutes(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return Math.round((asUtc - instant.getTime()) / 60000);
}

/** Convert a wall-clock date ("2026-10-14") and time ("10:00") in `timeZone` to a UTC ISO string. */
export function localToUtcIso(date: string, time: string, timeZone: string): string {
  const [y, mo, d] = date.split("-").map(Number);
  const [h, mi] = time.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  // Two passes settle the offset across a DST change.
  let utc = guess - offsetMinutes(new Date(guess), timeZone) * 60000;
  utc = guess - offsetMinutes(new Date(utc), timeZone) * 60000;
  return new Date(utc).toISOString().replace(".000Z", "Z");
}

/** Split a UTC ISO string into a wall-clock date and time in `timeZone`. */
export function utcToLocalParts(iso: string, timeZone: string): { date: string; time: string } {
  const instant = new Date(iso);
  const shifted = new Date(instant.getTime() + offsetMinutes(instant, timeZone) * 60000);
  const text = shifted.toISOString();
  return { date: text.slice(0, 10), time: text.slice(11, 16) };
}

export function addMinutes(time: string, minutes: number): string {
  const [h, m] = time.split(":").map(Number);
  const total = (h * 60 + m + minutes) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
